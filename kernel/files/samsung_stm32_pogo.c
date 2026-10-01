// SPDX-License-Identifier: GPL-2.0-only
/*
 * Samsung STM32 pogo keyboard controller, mainline-oriented subset.
 *
 * The wire protocol and input packet layout are derived from Samsung's GPLv2
 * SM-X910 Android 16 source release.  The untouched files and their hashes are
 * kept in kernel/vendor/samsung-stm32-pogo/.  This driver deliberately omits
 * Android sec_class/MUIC notifiers.  Its small firmware path accepts only the
 * measured official X910 size/version and verifies the complete flash before
 * booting it.  Power sequencing, both physical IRQs and the MAX77816 setup
 * follow the stock driver's measured path.
 *
 * Copyright (C) 2019-2026 Samsung Electronics
 */

#include <linux/bitmap.h>
#include <linux/delay.h>
#include <linux/firmware.h>
#include <linux/gpio/consumer.h>
#include <linux/i2c.h>
#include <linux/input.h>
#include <linux/input/mt.h>
#include <linux/interrupt.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/pm_wakeup.h>
#include <linux/property.h>
#include <linux/regulator/consumer.h>
#include <linux/unaligned.h>
#include <linux/workqueue.h>

#define POGO_EVENT_MCU		1
#define POGO_EVENT_TOUCHPAD	2
#define POGO_EVENT_KEYPAD	3
#define POGO_EVENT_HALL		4
#define POGO_EVENT_ACCESSORY	5

#define POGO_MAX_PAYLOAD	100
#define POGO_MODEL_EF_DX925	0xd3
#define POGO_MODEL_EF_DX920	0xd6
#define POGO_MODEL_EF_DX815	0xfb
#define POGO_HALL_LID_OPEN	2

#define MAX77816_CONFIG1	0x02
#define MAX77816_CONFIG2	0x03
#define MAX77816_LIMIT_3P1A	0x8e
#define MAX77816_OUTPUT_ENABLE	0x70
#define POGO_DISCONNECT_DEBOUNCE_MS	250
#define POGO_APP_I2C_RETRIES	3
#define POGO_APP_ID_MCU		1
#define POGO_APP_ID_TOUCHPAD	2
#define POGO_APP_CMD_GET_MODE	0x01
#define POGO_APP_CMD_VERSION	0x02
#define POGO_APP_CMD_CRC		0x03
#define POGO_APP_CMD_ABORT	0x17
#define POGO_APP_CMD_TC_VERSION	0x18
#define POGO_APP_CMD_TC_CRC	0x19
#define POGO_APP_CMD_TC_RESOLUTION 0x1a
#define POGO_APP_MODE_APP	1
#define POGO_APP_MODE_EXCEPTION	3
#define STM32_BOOT_I2C_ADDR	0x51
#define STM32_BOOT_ACK		0x79
#define STM32_FLASH_BASE	0x08000000
#define STM32_OPTION_BASE	0x1fff7800
#define STM32_FLASH_PAGE_SIZE	2048
#define STM32_FLASH_APP_PAGES	31
#define STM32_FW_SIZE		52132
#define STM32_FW_VERSION_OFFSET	0x200
#define STM32_FW_NAME		"keyboard_stm/stm32_gts9family.bin"

/*
 * GPIO62 can glitch while AOSS enters sleep on the SM-X910.  Keep connection
 * wake opt-in so attaching the keyboard cannot cause an immediate resume.
 */
static bool pogo_connection_wakeup;
module_param_named(connection_wakeup, pogo_connection_wakeup, bool, 0644);
MODULE_PARM_DESC(connection_wakeup,
		 "allow the pogo connection GPIO to wake the system");

struct samsung_pogo {
	struct i2c_client *client;
	struct i2c_client *booster;
	struct regulator *vddo;
	struct gpio_desc *data_ready;
	struct gpio_desc *connected;
	struct gpio_desc *boot;
	struct gpio_desc *reset;
	struct input_dev *input;
	struct input_dev *touchpad;
	u16 touchpad_max_x;
	u16 touchpad_max_y;
	struct delayed_work connection_work;
	struct delayed_work application_work;
	struct mutex lock;
	struct mutex recovery_lock;
	struct mutex power_lock;
	int data_irq;
	int connection_irq;
	DECLARE_BITMAP(keys_down, KEY_MAX + 1);
	u8 model;
	u8 caps_request;
	u8 flash_version[4];
	u8 app_version[4];
	bool attached;
	bool bootloader_reachable;
	bool data_irq_enabled;
	bool powered;
	bool reset_on_reconnect;
	bool wake_enabled;
	bool lid_closed;
	atomic64_t data_irq_count;
	atomic64_t data_irq_deasserted;
	atomic64_t connection_irq_high;
	atomic64_t connection_irq_low;
	atomic64_t manual_poll_count;
	atomic64_t key_event_count;
	atomic64_t recovery_count;
	atomic64_t read_retry_release_count;
	atomic64_t event_read_errors;
	atomic64_t recovery_gate_skips;
	atomic64_t invalid_key_events;
	int last_event_error;
	atomic64_t touchpad_packets;
	atomic64_t touchpad_bad_packets;
	atomic64_t init_attempts;
	int last_init_error;
};

static int samsung_pogo_input_event(struct input_dev *input,
				    unsigned int type, unsigned int code, int value);
static int samsung_pogo_initialize_application(struct samsung_pogo *pogo);
static void samsung_pogo_set_data_irq(struct samsung_pogo *pogo, bool enable);
static void samsung_pogo_release_keys(struct samsung_pogo *pogo);
static void samsung_pogo_release_touchpad(struct samsung_pogo *pogo);

static const char *samsung_pogo_model_name(u8 protocol_model,
					   const u8 version[4])
{
	/* EF-DX815 is the X810 cover-keyboard protocol model. */
	if (protocol_model == POGO_MODEL_EF_DX815)
		return "Book Cover Keyboard (EF-DX815)";
	/* Mirror stm32_read_version() from Samsung's X910 source. */
	if (protocol_model == 0xd1 || protocol_model == 0xd2)
		return "Book Cover Keyboard (EF-DX900)";
	if (protocol_model == 0x03 || protocol_model == POGO_MODEL_EF_DX925 ||
	    protocol_model == 0xd5)
		return "Book Cover Keyboard with AI Key (EF-DX925)";
	if (protocol_model == POGO_MODEL_EF_DX920)
		return "Book Cover Keyboard Slim with AI Key (EF-DX920)";

	switch (version[1]) {
	case 0:
		return "Book Cover Keyboard (EF-DX915)";
	case 1:
		return "Book Cover Keyboard Slim (EF-DX910)";
	case 2:
		return "Book Cover Keyboard (EF-DX900)";
	default:
		return NULL;
	}
}

static void samsung_pogo_power_off_locked(struct samsung_pogo *pogo)
{
	if (pogo->powered) {
		regulator_disable(pogo->vddo);
		pogo->powered = false;
	}
}

static void samsung_pogo_power_off(void *data)
{
	struct samsung_pogo *pogo = data;

	mutex_lock(&pogo->power_lock);
	samsung_pogo_power_off_locked(pogo);
	mutex_unlock(&pogo->power_lock);
}

static bool samsung_pogo_reset_application(struct samsung_pogo *pogo,
					   unsigned int settle_ms)
{
	bool connected;

	/* Serialize NRST against detach's VDDO-off/reset-hold sequence. */
	mutex_lock(&pogo->power_lock);
	connected = pogo->powered &&
		gpiod_get_value_cansleep(pogo->connected) > 0;
	if (connected) {
		gpiod_set_value_cansleep(pogo->boot, 0);
		gpiod_set_value_cansleep(pogo->reset, 1);
		usleep_range(3000, 4000);
		gpiod_set_value_cansleep(pogo->reset, 0);
	}
	mutex_unlock(&pogo->power_lock);
	if (!connected)
		return false;

	msleep(settle_ms);
	return true;
}

static void samsung_pogo_put_booster(void *data)
{
	put_device(data);
}

static int samsung_pogo_booster_write(struct samsung_pogo *pogo, u8 reg, u8 value)
{
	u8 data[] = { reg, value };
	struct i2c_msg msg;
	int retry;
	int ret = -EIO;

	/* X810 CYG1 has no stm32,booster_power_models property. */
	if (!pogo->booster)
		return 0;
	msg.addr = pogo->booster->addr;
	msg.flags = 0;
	msg.len = sizeof(data);
	msg.buf = data;

	/* kbd_i2c_write_ex() in Samsung's driver retries each write 3 times. */
	for (retry = 0; retry < 3; retry++) {
		ret = i2c_transfer(pogo->booster->adapter, &msg, 1);
		if (ret == 1)
			return 0;
	}

	return ret < 0 ? ret : -EIO;
}

static void samsung_pogo_start_application(struct samsung_pogo *pogo)
{
	/*
	 * Samsung's firmware-validation path always ends by selecting main flash
	 * and pulsing NRST before the normal keyboard protocol is enabled.  The
	 * STM32 otherwise remains silent at its application address even when both
	 * VDDO and the MAX77816 output are present.
	 */
	gpiod_set_value_cansleep(pogo->boot, 0);
	gpiod_set_value_cansleep(pogo->reset, 1);
	usleep_range(2000, 3000);
	gpiod_set_value_cansleep(pogo->reset, 0);
	msleep(150);
}


static int samsung_pogo_boot_write(struct samsung_pogo *pogo,
				   const void *buf, size_t len)
{
	struct i2c_msg msg = {
		.addr = STM32_BOOT_I2C_ADDR,
		.len = len,
		.buf = (void *)buf,
	};
	int ret = i2c_transfer(pogo->client->adapter, &msg, 1);

	return ret == 1 ? 0 : ret < 0 ? ret : -EIO;
}

static int samsung_pogo_boot_read(struct samsung_pogo *pogo, void *buf,
				  size_t len)
{
	struct i2c_msg msg = {
		.addr = STM32_BOOT_I2C_ADDR,
		.flags = I2C_M_RD,
		.len = len,
		.buf = buf,
	};
	int ret = i2c_transfer(pogo->client->adapter, &msg, 1);

	return ret == 1 ? 0 : ret < 0 ? ret : -EIO;
}

static int samsung_pogo_boot_ack(struct samsung_pogo *pogo)
{
	u8 ack;
	int ret = samsung_pogo_boot_read(pogo, &ack, sizeof(ack));

	if (ret)
		return ret;
	return ack == STM32_BOOT_ACK ? 0 : -EPROTO;
}

static u8 samsung_pogo_checksum(const u8 *data, size_t len)
{
	u8 checksum = 0;

	while (len--)
		checksum ^= *data++;
	return checksum;
}

static void samsung_pogo_enter_bootloader(struct samsung_pogo *pogo)
{
	/* Exact GPIO sequence from Samsung's stm32_sysboot_connect(). */
	gpiod_set_value_cansleep(pogo->reset, 1);
	gpiod_set_value_cansleep(pogo->boot, 1);
	usleep_range(3000, 4000);
	gpiod_set_value_cansleep(pogo->reset, 0);
	msleep(50);
	gpiod_set_value_cansleep(pogo->boot, 0);
}

static int samsung_pogo_boot_connect(struct samsung_pogo *pogo)
{
	const u8 sync = 0xff;
	int ret;

	samsung_pogo_enter_bootloader(pogo);
	ret = samsung_pogo_boot_write(pogo, &sync, sizeof(sync));
	if (ret)
		return ret;

	/* Required by the STM32 I2C boot protocol after its first SYNC. */
	samsung_pogo_enter_bootloader(pogo);
	return 0;
}

static int samsung_pogo_boot_read_memory(struct samsung_pogo *pogo, u32 address,
					 void *data, size_t len)
{
	const u8 command[] = { 0x11, 0xee };
	u8 start[5];
	u8 count[2];
	int ret;

	if (!len || len > 256)
		return -EINVAL;
	put_unaligned_be32(address, start);
	start[4] = samsung_pogo_checksum(start, 4);
	count[0] = len - 1;
	count[1] = ~count[0];

	ret = samsung_pogo_boot_write(pogo, command, sizeof(command));
	if (!ret)
		ret = samsung_pogo_boot_ack(pogo);
	if (!ret)
		ret = samsung_pogo_boot_write(pogo, start, sizeof(start));
	if (!ret)
		ret = samsung_pogo_boot_ack(pogo);
	if (!ret)
		ret = samsung_pogo_boot_write(pogo, count, sizeof(count));
	if (!ret)
		ret = samsung_pogo_boot_ack(pogo);
	if (!ret)
		ret = samsung_pogo_boot_read(pogo, data, len);
	return ret;
}

static int samsung_pogo_boot_write_memory(struct samsung_pogo *pogo, u32 address,
					  const u8 *data, size_t len)
{
	const u8 command[] = { 0x31, 0xce };
	u8 start[5];
	u8 packet[258];
	int ret;

	if (!len || len > 256)
		return -EINVAL;
	put_unaligned_be32(address, start);
	start[4] = samsung_pogo_checksum(start, 4);
	packet[0] = len - 1;
	memcpy(&packet[1], data, len);
	packet[len + 1] = samsung_pogo_checksum(packet, len + 1);

	ret = samsung_pogo_boot_write(pogo, command, sizeof(command));
	if (!ret)
		ret = samsung_pogo_boot_ack(pogo);
	if (!ret)
		ret = samsung_pogo_boot_write(pogo, start, sizeof(start));
	if (!ret)
		ret = samsung_pogo_boot_ack(pogo);
	if (!ret)
		ret = samsung_pogo_boot_write(pogo, packet, len + 2);
	if (!ret)
		ret = samsung_pogo_boot_ack(pogo);
	return ret;
}

static int samsung_pogo_boot_erase_application(struct samsung_pogo *pogo)
{
	const u8 command[] = { 0x44, 0xbb };
	u8 count[] = { 0x00, STM32_FLASH_APP_PAGES - 1,
			 STM32_FLASH_APP_PAGES - 1 };
	u8 pages[STM32_FLASH_APP_PAGES * 2 + 1];
	unsigned int page;
	int ret;

	for (page = 0; page < STM32_FLASH_APP_PAGES; page++)
		put_unaligned_be16(page, &pages[page * 2]);
	pages[sizeof(pages) - 1] = samsung_pogo_checksum(pages,
							sizeof(pages) - 1);

	ret = samsung_pogo_boot_write(pogo, command, sizeof(command));
	if (!ret)
		ret = samsung_pogo_boot_ack(pogo);
	if (!ret)
		ret = samsung_pogo_boot_write(pogo, count, sizeof(count));
	if (!ret)
		ret = samsung_pogo_boot_ack(pogo);
	if (!ret)
		ret = samsung_pogo_boot_write(pogo, pages, sizeof(pages));
	if (!ret) {
		msleep(1500);
		ret = samsung_pogo_boot_ack(pogo);
	}
	return ret;
}

static void samsung_pogo_probe_bootloader(struct samsung_pogo *pogo)
{
	const u8 sync = 0xff;
	const u8 get_id[] = { 0x02, 0xfd };
	const u8 read_memory[] = { 0x11, 0xee };
	const u8 version_address[] = { 0x08, 0x00, 0x02, 0x00, 0x0a };
	const u8 version_length[] = { 0x03, 0xfc };
	u8 info[3] = { 0 };
	u8 version[4] = { 0 };
	u8 option[4] = { 0 };
	u8 ack = 0;
	int ret;

	/*
	 * This is deliberately read-only.  Samsung performs the same handshake at
	 * every driver probe before returning the MCU to its main flash.  Besides
	 * selecting I2C system-boot mode, it tells us whether a silent application
	 * is caused by bus/reset wiring or by the contents of the STM32 flash.
	 */
	samsung_pogo_enter_bootloader(pogo);
	ret = samsung_pogo_boot_write(pogo, &sync, sizeof(sync));
	if (ret)
		goto out;

	/* Samsung re-enters system boot mode after the initial I2C SYNC. */
	samsung_pogo_enter_bootloader(pogo);
	ret = samsung_pogo_boot_write(pogo, get_id, sizeof(get_id));
	if (!ret)
		ret = samsung_pogo_boot_read(pogo, &ack, sizeof(ack));
	if (!ret && ack == STM32_BOOT_ACK)
		ret = samsung_pogo_boot_read(pogo, info, sizeof(info));
	if (!ret)
		ret = samsung_pogo_boot_read(pogo, &ack, sizeof(ack));
	if (ret || ack != STM32_BOOT_ACK)
		goto out;

	ret = samsung_pogo_boot_write(pogo, read_memory, sizeof(read_memory));
	if (!ret)
		ret = samsung_pogo_boot_read(pogo, &ack, sizeof(ack));
	if (ret || ack != STM32_BOOT_ACK)
		goto out;
	ret = samsung_pogo_boot_write(pogo, version_address,
				      sizeof(version_address));
	if (!ret)
		ret = samsung_pogo_boot_read(pogo, &ack, sizeof(ack));
	if (ret || ack != STM32_BOOT_ACK)
		goto out;
	ret = samsung_pogo_boot_write(pogo, version_length,
				      sizeof(version_length));
	if (!ret)
		ret = samsung_pogo_boot_read(pogo, &ack, sizeof(ack));
	if (!ret && ack == STM32_BOOT_ACK)
		ret = samsung_pogo_boot_read(pogo, version, sizeof(version));
	if (!ret)
		ret = samsung_pogo_boot_read_memory(pogo, STM32_OPTION_BASE,
						     option, sizeof(option));

out:
	if (!ret && ack == STM32_BOOT_ACK) {
		pogo->bootloader_reachable = true;
		memcpy(pogo->flash_version, version, sizeof(version));
		dev_info(&pogo->client->dev,
			 "STM32 bootloader reachable, product id %#04x, flash version %*ph, option bytes %*ph\n",
			 get_unaligned_be16(&info[1]), (int)sizeof(version), version,
			 (int)sizeof(option), option);
	} else {
		dev_warn(&pogo->client->dev,
			 "STM32 bootloader probe failed: %d (ack=%#x)\n",
			 ret ?: -EPROTO, ack);
	}

	samsung_pogo_start_application(pogo);
}

static int samsung_pogo_update_firmware(struct samsung_pogo *pogo,
					const struct firmware *firmware)
{
	u8 verify[256];
	size_t offset;
	int ret;

	ret = samsung_pogo_boot_connect(pogo);
	if (ret)
		goto out_start_app;

	dev_info(&pogo->client->dev, "erasing STM32 application pages\n");
	ret = samsung_pogo_boot_erase_application(pogo);
	if (ret)
		goto out_start_app;

	for (offset = 0; offset < firmware->size; offset += sizeof(verify)) {
		size_t len = min_t(size_t, sizeof(verify), firmware->size - offset);

		ret = samsung_pogo_boot_write_memory(pogo,
				STM32_FLASH_BASE + offset, firmware->data + offset, len);
		if (ret)
			goto out_start_app;
		if (!(offset & 0x1fff))
			dev_info(&pogo->client->dev, "STM32 programmed %zu/%zu bytes\n",
				 offset + len, firmware->size);
		cond_resched();
	}

	for (offset = 0; offset < firmware->size; offset += sizeof(verify)) {
		size_t len = min_t(size_t, sizeof(verify), firmware->size - offset);

		ret = samsung_pogo_boot_read_memory(pogo,
				STM32_FLASH_BASE + offset, verify, len);
		if (ret)
			goto out_start_app;
		if (memcmp(verify, firmware->data + offset, len)) {
			ret = -EBADMSG;
			goto out_start_app;
		}
		cond_resched();
	}

	memcpy(pogo->flash_version,
	       firmware->data + STM32_FW_VERSION_OFFSET,
	       sizeof(pogo->flash_version));
	dev_info(&pogo->client->dev,
		 "STM32 firmware programmed and fully verified (%zu bytes, version %*ph)\n",
		 firmware->size, (int)sizeof(pogo->flash_version),
		 pogo->flash_version);

out_start_app:
	samsung_pogo_start_application(pogo);
	return ret;
}

static ssize_t firmware_update_store(struct device *dev,
				     struct device_attribute *attribute,
				     const char *buf, size_t count)
{
	static const u8 expected_version[] = { 0x00, 0x37, 0x00, 0x37 };
	struct samsung_pogo *pogo = dev_get_drvdata(dev);
	const struct firmware *firmware;
	int ret;

	if (!sysfs_streq(buf, "1"))
		return -EINVAL;
	if (!pogo->bootloader_reachable)
		return -ENODEV;
	if (!memcmp(pogo->flash_version, expected_version,
		    sizeof(expected_version))) {
		dev_info(dev, "STM32 firmware is already current\n");
		return count;
	}

	ret = request_firmware(&firmware, STM32_FW_NAME, dev);
	if (ret)
		return ret;
	if (firmware->size != STM32_FW_SIZE ||
	    memcmp(firmware->data + STM32_FW_VERSION_OFFSET,
		   expected_version, sizeof(expected_version))) {
		dev_err(dev, "refusing unexpected STM32 firmware (%zu bytes, version %*ph)\n",
			firmware->size, (int)sizeof(expected_version),
			firmware->size >= STM32_FW_VERSION_OFFSET + sizeof(expected_version) ?
			firmware->data + STM32_FW_VERSION_OFFSET : expected_version);
		ret = -EINVAL;
		goto out_release;
	}

	/*
	 * No cover required.  This used to refuse unless the connection GPIO was
	 * high, on the assumption that the controller rode the same rail as the
	 * accessory.  Measured with the cover detached, pogo_vddo disabled and
	 * connected=0, the ROM bootloader still answered with its product id and
	 * flash version: the rail that is cut feeds the keyboard, not the MCU,
	 * which is on the tablet's own I2C6 and independent of it.
	 *
	 * Dropping the check is what lets a fresh install repair itself without
	 * the owner knowing there was anything to repair, and writing with no
	 * cover attached is if anything quieter: no connection pulses to race.
	 */
	disable_irq(pogo->connection_irq);
	cancel_delayed_work_sync(&pogo->connection_work);
	samsung_pogo_set_data_irq(pogo, false);
	cancel_delayed_work_sync(&pogo->application_work);
	mutex_lock(&pogo->lock);
	ret = samsung_pogo_update_firmware(pogo, firmware);
	pogo->attached = false;
	pogo->model = 0;
	mutex_unlock(&pogo->lock);
	enable_irq(pogo->connection_irq);
	mod_delayed_work(system_dfl_wq, &pogo->connection_work, 0);

out_release:
	release_firmware(firmware);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(firmware_update);

/*
 * A driver reprobe recovers a wedged controller because probe deliberately
 * enters the STM32 system bootloader (BOOT0/NRST), reads its immutable ID and
 * flash version, and returns it to the application.  A physical keyboard
 * disconnect only cuts the accessory VDDO rail; on this board that does not
 * necessarily reset the tablet-side STM32.  Expose the same read-only
 * bootloader handshake/application restart as an explicit, privileged
 * recovery operation.  This never erases or programs STM32 flash.
 */
static ssize_t recover_store(struct device *dev,
			     struct device_attribute *attribute,
			     const char *buf, size_t count)
{
	struct samsung_pogo *pogo = dev_get_drvdata(dev);
	bool connected;
	int ret;

	if (!sysfs_streq(buf, "1"))
		return -EINVAL;

	/* Serialize the whole IRQ/work quiesce sequence, not just the reset. */
	mutex_lock(&pogo->recovery_lock);

	/* Quiesce both interrupt paths before manipulating BOOT0/NRST. */
	disable_irq(pogo->connection_irq);
	cancel_delayed_work_sync(&pogo->connection_work);
	samsung_pogo_set_data_irq(pogo, false);
	cancel_delayed_work_sync(&pogo->application_work);

	mutex_lock(&pogo->lock);
	connected = gpiod_get_value_cansleep(pogo->connected) > 0;
	if (!pogo->attached || !pogo->powered || !connected) {
		ret = -ENODEV;
		goto out_unlock;
	}

	/* Do not leave a modifier or touch contact logically held over reset. */
	samsung_pogo_release_keys(pogo);
	pogo->bootloader_reachable = false;
	samsung_pogo_probe_bootloader(pogo);
	ret = samsung_pogo_initialize_application(pogo);
	pogo->last_init_error = ret;
	atomic64_inc(&pogo->recovery_count);
	if (ret)
		dev_warn(dev, "manual controller recovery failed: %d\n", ret);
	else
		dev_info(dev, "manual controller recovery completed\n");

out_unlock:
	if (pogo->attached && gpiod_get_value_cansleep(pogo->connected) > 0)
		samsung_pogo_set_data_irq(pogo, true);
	mutex_unlock(&pogo->lock);

	/* Reconcile any physical change that occurred while its IRQ was masked. */
	enable_irq(pogo->connection_irq);
	mod_delayed_work(system_dfl_wq, &pogo->connection_work, 0);
	mutex_unlock(&pogo->recovery_lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_WO(recover);

static int samsung_pogo_enable_power(struct samsung_pogo *pogo)
{
	int ret;

	mutex_lock(&pogo->power_lock);
	if (pogo->powered) {
		ret = 0;
		goto out_unlock;
	}

	ret = regulator_enable(pogo->vddo);
	if (ret)
		goto out_unlock;
	pogo->powered = true;

	/* X910 specifies an optional MAX77816 booster. X810 CYG1 does not. */
	ret = 0;
	if (pogo->booster) {
		ret = samsung_pogo_booster_write(pogo, MAX77816_CONFIG2,
						 MAX77816_OUTPUT_ENABLE);
		if (!ret)
			ret = samsung_pogo_booster_write(pogo, MAX77816_CONFIG1,
						     MAX77816_LIMIT_3P1A);
	}
	if (ret) {
		samsung_pogo_power_off_locked(pogo);
		goto out_unlock;
	}

	if (pogo->booster)
		dev_info(&pogo->client->dev,
			 "MAX77816 output enabled (config2=%#x, config1=%#x)\n",
			 MAX77816_OUTPUT_ENABLE, MAX77816_LIMIT_3P1A);
out_unlock:
	mutex_unlock(&pogo->power_lock);
	return ret;
}

static int samsung_pogo_app_send(struct samsung_pogo *pogo,
				 const void *data, size_t len)
{
	int retry;
	int ret = -EIO;

	for (retry = 0; retry < POGO_APP_I2C_RETRIES; retry++) {
		ret = i2c_master_send(pogo->client, data, len);
		if (ret == len)
			return 0;
		usleep_range(1000, 1500);
	}

	return ret < 0 ? ret : -EIO;
}

static int samsung_pogo_app_recv(struct samsung_pogo *pogo, void *data,
				 size_t len)
{
	int retry;
	int ret = -EIO;

	for (retry = 0; retry < POGO_APP_I2C_RETRIES; retry++) {
		ret = i2c_master_recv(pogo->client, data, len);
		if (ret == len)
			return 0;

		/*
		 * Samsung notifies every input consumer to release its state on the
		 * first failed read, before retrying the same transfer.  In particular,
		 * do not retain a preceding key press for up to three GENI timeouts while
		 * waiting for its release packet.  The final exhausted-retry path below
		 * still resets the STM32 exactly as before.
		 */
		samsung_pogo_release_keys(pogo);
		atomic64_inc(&pogo->read_retry_release_count);
		msleep(10);
	}

	return ret < 0 ? ret : -EIO;
}

static int samsung_pogo_send_header(struct samsung_pogo *pogo)
{
	u8 header[] = { 3, 0, READ_ONCE(pogo->caps_request) };

	return samsung_pogo_app_send(pogo, header, sizeof(header));
}

static int samsung_pogo_recv(struct samsung_pogo *pogo, void *buf, size_t len)
{
	return samsung_pogo_app_recv(pogo, buf, len);
}

static int samsung_pogo_app_read_reg(struct samsung_pogo *pogo, u8 id,
				     u8 reg, void *data, size_t len)
{
	u8 header[] = { 4, 0, id };
	u8 response[3];
	u16 total;
	int ret;

	ret = samsung_pogo_app_send(pogo, header, sizeof(header));
	if (!ret)
		ret = samsung_pogo_app_send(pogo, &reg, sizeof(reg));
	if (!ret)
		ret = samsung_pogo_app_recv(pogo, response, sizeof(response));
	if (ret)
		return ret;

	total = get_unaligned_le16(response);
	if (total != len + sizeof(response) || response[2] != id)
		return -EPROTO;

	return samsung_pogo_app_recv(pogo, data, len);
}

static int samsung_pogo_app_write_reg(struct samsung_pogo *pogo, u8 id, u8 reg)
{
	u8 header[] = { 4, 0, id };
	int ret;

	ret = samsung_pogo_app_send(pogo, header, sizeof(header));
	if (!ret)
		ret = samsung_pogo_app_send(pogo, &reg, sizeof(reg));
	return ret;
}

static int samsung_pogo_initialize_application(struct samsung_pogo *pogo)
{
	u8 crc[4];
	u8 tc_version[6];
	u8 tc_crc[2], tc_resolution[4];
	u8 mode;
	int ret;

	ret = samsung_pogo_app_read_reg(pogo, POGO_APP_ID_MCU,
					POGO_APP_CMD_GET_MODE, &mode,
					sizeof(mode));
	if (ret)
		return ret;
	if (mode != POGO_APP_MODE_APP && mode != POGO_APP_MODE_EXCEPTION) {
		ret = samsung_pogo_app_write_reg(pogo, POGO_APP_ID_MCU,
						 POGO_APP_CMD_ABORT);
		if (ret)
			return ret;
	}
	msleep(200);

	ret = samsung_pogo_app_read_reg(pogo, POGO_APP_ID_MCU,
					POGO_APP_CMD_CRC, crc, sizeof(crc));
	if (ret)
		return ret;
	ret = samsung_pogo_app_read_reg(pogo, POGO_APP_ID_TOUCHPAD,
					POGO_APP_CMD_TC_VERSION, tc_version,
					sizeof(tc_version));
	if (ret)
		return ret;

	/* Finish Samsung's touch-controller handshake on covers with a pad. */
	if (pogo->touchpad && (get_unaligned_le16(tc_version + 2) != 0xff ||
			     get_unaligned_le16(tc_version + 4) != 0)) {
		ret = samsung_pogo_app_read_reg(pogo, POGO_APP_ID_TOUCHPAD,
			POGO_APP_CMD_TC_CRC, tc_crc, sizeof(tc_crc));
		if (!ret)
			ret = samsung_pogo_app_read_reg(pogo, POGO_APP_ID_TOUCHPAD,
				POGO_APP_CMD_TC_RESOLUTION, tc_resolution, sizeof(tc_resolution));
		if (ret)
			return ret;
		dev_info(&pogo->client->dev, "touchpad controller: CRC %*ph, resolution %ux%u\n",
			 (int)sizeof(tc_crc), tc_crc,
			 get_unaligned_le16(tc_resolution + 2), get_unaligned_le16(tc_resolution));
	}

	dev_info(&pogo->client->dev,
		 "application initialized: version %*ph, mode %u, CRC %*ph, accessory %*ph\n",
		 (int)sizeof(pogo->app_version), pogo->app_version, mode,
		 (int)sizeof(crc), crc,
		 (int)sizeof(tc_version), tc_version);
	return 0;
}

static void samsung_pogo_application_work(struct work_struct *work)
{
	struct samsung_pogo *pogo = container_of(to_delayed_work(work),
						  struct samsung_pogo,
						  application_work);
	int ret;

	mutex_lock(&pogo->lock);
	if (!pogo->attached || !pogo->input)
		goto out_unlock;

	/* Samsung runs check_ic_work for every supported cover, not just Slim. */
	atomic64_inc(&pogo->init_attempts);
	ret = samsung_pogo_initialize_application(pogo);
	pogo->last_init_error = ret;
	if (ret)
		dev_warn(&pogo->client->dev,
			 "application initialization failed: %d\n", ret);

out_unlock:
	mutex_unlock(&pogo->lock);
}

static void samsung_pogo_release_keys(struct samsung_pogo *pogo)
{
	unsigned int code;
	bool changed = false;
	samsung_pogo_release_touchpad(pogo);

	if (!pogo->input) {
		bitmap_zero(pogo->keys_down, KEY_MAX + 1);
		return;
	}

	for_each_set_bit(code, pogo->keys_down, KEY_MAX + 1) {
		input_report_key(pogo->input, code, 0);
		changed = true;
	}
	bitmap_zero(pogo->keys_down, KEY_MAX + 1);
	if (changed)
		input_sync(pogo->input);
}

static void samsung_pogo_release_touchpad(struct samsung_pogo *pogo)
{
	int slot;

	if (!pogo->touchpad)
		return;
	for (slot = 0; slot < 3; slot++) {
		input_mt_slot(pogo->touchpad, slot);
		input_mt_report_slot_state(pogo->touchpad, MT_TOOL_FINGER, false);
	}
	input_report_key(pogo->touchpad, BTN_LEFT, 0);
	input_mt_sync_frame(pogo->touchpad);
	input_sync(pogo->touchpad);
}

static int samsung_pogo_register_touchpad(struct samsung_pogo *pogo)
{
	struct input_dev *input;
	int ret;

	if (pogo->touchpad)
		return 0;
	/* Dimensions and model IDs from Samsung stm32_pogo_touchpad_v3.c. */
	switch (pogo->model) {
	case 0xf9: /* EF-DX915 */
	case 0xfe: /* EF-DX900 */
	case POGO_MODEL_EF_DX925:
		pogo->touchpad_max_x = 1764;
		pogo->touchpad_max_y = 1072;
		break;
	case 0xfd:
	case 0xfb:
	case 0xd1:
	case 0xd2:
		pogo->touchpad_max_x = 1560;
		pogo->touchpad_max_y = 820;
		break;
	default:
		/* Do not create a phantom touchpad on Slim or unknown models. */
		return 0;
	}
	input = input_allocate_device();
	if (!input)
		return -ENOMEM;
	/* Keep it out of the existing Companion keyboard-remapping name match. */
	input->name = "Samsung Book Cover Touchpad";
	input->phys = "samsung-pogo/input1";
	input->dev.parent = &pogo->client->dev;
	input->id.bustype = BUS_I2C;
	input->id.vendor = 0x04e8;
	input->id.product = 0xa035;
	__set_bit(INPUT_PROP_BUTTONPAD, input->propbit);
	input_set_capability(input, EV_KEY, BTN_LEFT);
	input_set_abs_params(input, ABS_MT_POSITION_X, 0, pogo->touchpad_max_x - 1, 0, 0);
	input_set_abs_params(input, ABS_MT_POSITION_Y, 0, pogo->touchpad_max_y - 1, 0, 0);
	input_set_abs_params(input, ABS_MT_TOUCH_MAJOR, 0, 255, 0, 0);
	input_abs_set_res(input, ABS_MT_POSITION_X, 15);
	input_abs_set_res(input, ABS_MT_POSITION_Y, 15);
	ret = input_mt_init_slots(input, 3, INPUT_MT_POINTER);
	if (!ret)
		ret = input_register_device(input);
	if (ret) {
		input_free_device(input);
		return ret;
	}
	pogo->touchpad = input;
	dev_info(&pogo->client->dev, "touchpad enabled: %ux%u, three slots\n",
		 pogo->touchpad_max_x, pogo->touchpad_max_y);
	return 0;
}

static void samsung_pogo_report_touchpad(struct samsung_pogo *pogo,
				       const u8 *payload, size_t len)
{
	struct input_dev *input = pogo->touchpad;
	u16 status;
	int slot;

	atomic64_inc(&pogo->touchpad_packets);
	if (!input)
		return;
	/* Samsung's packed point_data: status, count, button, 3 x six bytes. */
	if (len != 22 || (payload[2] & 0xf) > 3)
		goto malformed;
	status = get_unaligned_le16(payload);
	/* Validate the complete frame before changing any input state. */
	for (slot = 0; slot < 3; slot++) {
		const u8 *point = payload + 4 + 6 * slot;

		if ((status & BIT(11)) && (point[5] & BIT(0)) &&
		    (get_unaligned_le16(point) >= pogo->touchpad_max_x ||
		     get_unaligned_le16(point + 2) >= pogo->touchpad_max_y))
			goto malformed;
	}
	if (status & BIT(15))
		input_report_key(input, BTN_LEFT, payload[3] & BIT(0));
	for (slot = 0; slot < 3; slot++) {
		const u8 *point = payload + 4 + 6 * slot;
		bool active = (status & BIT(11)) && (point[5] & BIT(0));

		input_mt_slot(input, slot);
		input_mt_report_slot_state(input, MT_TOOL_FINGER, active);
		if (!active)
			continue;
		input_report_abs(input, ABS_MT_POSITION_X, get_unaligned_le16(point));
		input_report_abs(input, ABS_MT_POSITION_Y, get_unaligned_le16(point + 2));
		input_report_abs(input, ABS_MT_TOUCH_MAJOR, point[4]);
	}
	input_mt_sync_frame(input);
	input_sync(input);
	return;
malformed:
	atomic64_inc(&pogo->touchpad_bad_packets);
	samsung_pogo_release_touchpad(pogo);
	dev_warn_ratelimited(&pogo->client->dev, "invalid touchpad frame (%zu bytes)\n", len);
}

static int samsung_pogo_register_input(struct samsung_pogo *pogo,
				       const char *model_name)
{
	struct device *dev = &pogo->client->dev;
	struct input_dev *input;
	unsigned int code;
	int ret;

	if (pogo->input)
		return 0;

	input = input_allocate_device();
	if (!input)
		return -ENOMEM;

	input->name = model_name;
	input->phys = "samsung-pogo/input0";
	input->dev.parent = dev;
	input->id.bustype = BUS_I2C;
	input->id.vendor = 0x04e8;
	input->id.product = 0xa035;
	input->event = samsung_pogo_input_event;
	input_set_drvdata(input, pogo);
	for (code = 1; code <= KEY_MAX; code++)
		input_set_capability(input, EV_KEY, code);
	input_set_capability(input, EV_LED, LED_CAPSL);
	input_set_capability(input, EV_SW, SW_LID);

	ret = input_register_device(input);
	if (ret) {
		input_free_device(input);
		return ret;
	}

	pogo->input = input;
	return 0;
}

static void samsung_pogo_unregister_input(struct samsung_pogo *pogo)
{
	struct input_dev *input = pogo->input;

	if (!input)
		return;

	samsung_pogo_release_keys(pogo);
	if (pogo->touchpad) {
		input_unregister_device(pogo->touchpad);
		pogo->touchpad = NULL;
	}
	pogo->input = NULL;
	input_unregister_device(input);
}

static int samsung_pogo_report_keys(struct samsung_pogo *pogo,
				    const u8 *payload, size_t len)
{
	size_t offset;

	if (!pogo->input)
		return 0;

	for (offset = 0; offset + sizeof(u16) <= len; offset += sizeof(u16)) {
		u16 event = get_unaligned_le16(payload + offset);
		unsigned int code = event & GENMASK(14, 0);
		bool pressed = event & BIT(15);

		/* Sentinels emitted at an idle poll and immediately after reset. */
		if (event == 0xffff || event == 0x7fff)
			continue;

		atomic64_inc(&pogo->key_event_count);

		if (!code || code > KEY_MAX) {
			atomic64_inc(&pogo->invalid_key_events);
			dev_warn_ratelimited(&pogo->client->dev,
				"invalid key event\n");
			/* Use 0003's existing safe reset/defer path on malformed frames. */
			return -EPROTO;
		}

		if (pressed)
			__set_bit(code, pogo->keys_down);
		else
			__clear_bit(code, pogo->keys_down);
		input_report_key(pogo->input, code, pressed);
	}
	input_sync(pogo->input);
	return 0;
}

static void samsung_pogo_report_hall(struct samsung_pogo *pogo,
				     const u8 *payload, size_t len)
{
	bool closed;

	if (!len || !pogo->input)
		return;

	closed = payload[0] != POGO_HALL_LID_OPEN;
	if (closed == pogo->lid_closed)
		return;

	pogo->lid_closed = closed;
	input_report_switch(pogo->input, SW_LID, closed);
	input_sync(pogo->input);
	dev_info(&pogo->client->dev, "keyboard cover %s\n",
		 closed ? "closed" : "open");
}

static int samsung_pogo_read_event(struct samsung_pogo *pogo)
{
	u8 header[3];
	u8 payload[POGO_MAX_PAYLOAD];
	u16 total;
	size_t payload_len;
	int ret;

	ret = samsung_pogo_send_header(pogo);
	if (ret)
		return ret;

	ret = samsung_pogo_recv(pogo, header, sizeof(header));
	if (ret)
		return ret;

	total = get_unaligned_le16(header);
	if (total <= sizeof(header) || total - sizeof(header) > sizeof(payload)) {
		/* Samsung uses this otherwise-invalid header as the attach/model event. */
		if (header[2] && header[2] != 0xff) {
			const char *model_name;

			pogo->model = header[2];
			/*
			 * Samsung reads VERSION synchronously in this IRQ, then
			 * releases it and schedules check_ic_work 10 ms later.  The
			 * version's model id disambiguates the older DX900/910/915;
			 * newer AI covers use explicit protocol ids.
			 */
			ret = samsung_pogo_app_read_reg(pogo, POGO_APP_ID_MCU,
					POGO_APP_CMD_VERSION, pogo->app_version,
					sizeof(pogo->app_version));
			if (ret) {
				dev_warn(&pogo->client->dev,
					 "immediate version read failed: %d\n", ret);
				return 0;
			}
			model_name = samsung_pogo_model_name(pogo->model,
							    pogo->app_version);
			if (!model_name) {
				dev_warn(&pogo->client->dev,
					 "unsupported keyboard model %#02x, version %*ph\n",
					 pogo->model, (int)sizeof(pogo->app_version),
					 pogo->app_version);
				return 0;
			}
			dev_info(&pogo->client->dev,
				 "keyboard attached, model %#02x (%s)\n",
				 pogo->model, model_name);
			mod_delayed_work(system_dfl_wq, &pogo->application_work,
					 msecs_to_jiffies(10));
			if (!pogo->input) {
				ret = samsung_pogo_register_input(pogo, model_name);
				if (ret)
					return ret;
				dev_info(&pogo->client->dev,
					 "%s protocol confirmed; input enabled\n",
					 model_name);
			}
			ret = samsung_pogo_register_touchpad(pogo);
			if (ret)
				dev_warn(&pogo->client->dev, "touchpad registration failed: %d\n", ret);
		}
		return 0;
	}

	payload_len = total - sizeof(header);
	ret = samsung_pogo_recv(pogo, payload, payload_len);
	if (ret)
		return ret;

	switch (header[2]) {
	case POGO_EVENT_TOUCHPAD:
		samsung_pogo_report_touchpad(pogo, payload, payload_len);
		break;
	case POGO_EVENT_KEYPAD:
		ret = samsung_pogo_report_keys(pogo, payload, payload_len);
		if (ret)
			return ret;
		break;
	case POGO_EVENT_HALL:
		samsung_pogo_release_keys(pogo);
		samsung_pogo_report_hall(pogo, payload, payload_len);
		break;
	case POGO_EVENT_ACCESSORY:
		dev_dbg(&pogo->client->dev, "accessory packet (%zu bytes)\n",
			payload_len);
		break;
	case POGO_EVENT_MCU:
	default:
		dev_dbg(&pogo->client->dev, "event %u (%zu bytes)\n",
			header[2], payload_len);
		break;
	}

	return 0;
}

static irqreturn_t samsung_pogo_irq(int irq, void *data)
{
	struct samsung_pogo *pogo = data;
	int data_ready;

	atomic64_inc(&pogo->data_irq_count);
	/*
	 * Sample DATA in hard-IRQ context, while the active-low request is current.
	 * Doing this in the threaded handler loses short but valid pulses: DATA
	 * can be high by the time that thread runs even though the corresponding
	 * key packet is still queued.  Conversely, a stale pending request delivered
	 * after ONESHOT unmasks the line must not poll an empty STM32 queue.
	 * GPIO75 belongs to TLMM and therefore does not sleep.
	 */
	data_ready = gpiod_get_value(pogo->data_ready);
	if (data_ready <= 0) {
		atomic64_inc(&pogo->data_irq_deasserted);
		return IRQ_HANDLED;
	}

	return IRQ_WAKE_THREAD;
}

static irqreturn_t samsung_pogo_irq_thread(int irq, void *data)
{
	struct samsung_pogo *pogo = data;
	bool reset = false;
	bool deferred = false;
	bool error_attached = false;
	bool error_powered = false;
	bool error_irq_enabled = false;
	bool error_reset_pending = false;
	bool error_input = false;
	int error_connected = -1;
	int error_data_ready = -1;
	int connected;
	int ret;

	pm_wakeup_event(&pogo->client->dev, 1000);
	mutex_lock(&pogo->lock);
	ret = pogo->attached ? samsung_pogo_read_event(pogo) : -ENODEV;
	if (ret && ret != -ENODEV) {
		atomic64_inc(&pogo->event_read_errors);
		WRITE_ONCE(pogo->last_event_error, ret);
		error_attached = pogo->attached;
		error_powered = READ_ONCE(pogo->powered);
		error_connected = gpiod_get_value_cansleep(pogo->connected);
		error_data_ready = gpiod_get_value_cansleep(pogo->data_ready);
		error_irq_enabled = READ_ONCE(pogo->data_irq_enabled);
		error_reset_pending = READ_ONCE(pogo->reset_on_reconnect);
		error_input = pogo->input != NULL;
	}
	if (ret && ret != -ENODEV && pogo->attached) {
		connected = gpiod_get_value_cansleep(pogo->connected);
		if (READ_ONCE(pogo->powered) && connected > 0) {
			/*
			 * Samsung resets after exhausted application-I2C retries.
			 * Serialize that pulse with VDDO shutdown; if attachment raced
			 * away, defer it to connection reconciliation below.
			 */
			cancel_delayed_work(&pogo->application_work);
			samsung_pogo_release_keys(pogo);
			pogo->model = 0;
			if (samsung_pogo_reset_application(pogo, 10)) {
				atomic64_inc(&pogo->recovery_count);
				reset = true;
			} else {
				WRITE_ONCE(pogo->reset_on_reconnect, true);
				mod_delayed_work(system_dfl_wq, &pogo->connection_work,
						 msecs_to_jiffies(POGO_DISCONNECT_DEBOUNCE_MS));
				deferred = true;
			}
		} else {
			/*
			 * The transfer exhausted retries while connection/power was
			 * changing. Preserve reset intent; the worker will pulse NRST
			 * after power returns instead of silently abandoning recovery.
			 */
			WRITE_ONCE(pogo->reset_on_reconnect, true);
			mod_delayed_work(system_dfl_wq, &pogo->connection_work,
					 msecs_to_jiffies(POGO_DISCONNECT_DEBOUNCE_MS));
			deferred = true;
		}
	}
	if (ret && ret != -ENODEV && !reset)
		atomic64_inc(&pogo->recovery_gate_skips);
	mutex_unlock(&pogo->lock);
	if (ret && ret != -ENODEV)
		dev_warn_ratelimited(&pogo->client->dev,
				     "event read failed: %d attached=%u powered=%u connected=%d data_ready=%d data_irq_enabled=%u reset_pending=%u input=%u%s%s\n",
				     ret, error_attached, error_powered, error_connected,
				     error_data_ready, error_irq_enabled, error_reset_pending,
				     error_input,
				     reset ? "; application reset" : "",
				     deferred ? "; recovery deferred" : "");

	return IRQ_HANDLED;
}

static void samsung_pogo_set_data_irq(struct samsung_pogo *pogo, bool enable)
{
	if (enable == pogo->data_irq_enabled)
		return;

	if (enable)
		enable_irq(pogo->data_irq);
	else
		disable_irq(pogo->data_irq);
	pogo->data_irq_enabled = enable;
}

static void samsung_pogo_connection_work(struct work_struct *work)
{
	struct samsung_pogo *pogo = container_of(to_delayed_work(work),
						  struct samsung_pogo, connection_work);
	struct device *dev = &pogo->client->dev;
	bool connected;
	int ret;

	connected = gpiod_get_value_cansleep(pogo->connected) > 0;

	/* Wait for an in-flight data thread before destroying its input device. */
	if (!connected || READ_ONCE(pogo->reset_on_reconnect)) {
		samsung_pogo_set_data_irq(pogo, false);
		cancel_delayed_work_sync(&pogo->application_work);
	}

	mutex_lock(&pogo->lock);
	/*
	 * Samsung restores VDDO on a short reconnect pulse without changing the
	 * debounced attachment state. Its connection ISR switched it off at the
	 * falling edge below.
	 */
	if (connected && pogo->attached &&
	    (!pogo->powered || READ_ONCE(pogo->reset_on_reconnect))) {
		if (!pogo->powered) {
			ret = samsung_pogo_enable_power(pogo);
			if (ret) {
				dev_err_ratelimited(dev,
						     "cannot restore keyboard power: %d\n",
						     ret);
				mod_delayed_work(system_dfl_wq, &pogo->connection_work,
							 msecs_to_jiffies(1000));
				goto out_unlock;
			}
		}
		if (READ_ONCE(pogo->reset_on_reconnect)) {
			/*
			 * A failed runtime read can race with VDDO/connection loss.
			 * Keep recovery pending until the rail and GPIO62 are valid,
			 * then perform a complete application reset before data IRQs.
			 */
			WRITE_ONCE(pogo->reset_on_reconnect, false);
			msleep(50);
			if (!samsung_pogo_reset_application(pogo, 150)) {
				WRITE_ONCE(pogo->reset_on_reconnect, true);
				mod_delayed_work(system_dfl_wq, &pogo->connection_work,
							 msecs_to_jiffies(POGO_DISCONNECT_DEBOUNCE_MS));
			}
			if (!READ_ONCE(pogo->reset_on_reconnect))
				samsung_pogo_set_data_irq(pogo, true);
		}
	}
	if (connected == pogo->attached)
		goto out_unlock;

	if (connected) {
		ret = samsung_pogo_enable_power(pogo);
		if (ret) {
			dev_err_ratelimited(dev, "cannot power keyboard: %d\n", ret);
			mod_delayed_work(system_dfl_wq, &pogo->connection_work,
					 msecs_to_jiffies(1000));
			goto out_unlock;
		}

		msleep(50);
		if (READ_ONCE(pogo->reset_on_reconnect)) {
			WRITE_ONCE(pogo->reset_on_reconnect, false);
			if (!samsung_pogo_reset_application(pogo, 150))
				WRITE_ONCE(pogo->reset_on_reconnect, true);
		}
		if (READ_ONCE(pogo->reset_on_reconnect)) {
			mod_delayed_work(system_dfl_wq, &pogo->connection_work,
					 msecs_to_jiffies(POGO_DISCONNECT_DEBOUNCE_MS));
			goto out_unlock;
		}
		pogo->attached = true;
		samsung_pogo_set_data_irq(pogo, true);
		dev_info(dev, "pogo connection detected; waiting for protocol ID\n");
	} else {
		samsung_pogo_unregister_input(pogo);
		pogo->attached = false;
		pogo->model = 0;
		pogo->caps_request = 1;
		pogo->lid_closed = false;
		samsung_pogo_power_off(pogo);
		dev_info(dev, "keyboard physically disconnected\n");
	}

out_unlock:
	mutex_unlock(&pogo->lock);
}

static irqreturn_t samsung_pogo_connection_irq_thread(int irq, void *data)
{
	struct samsung_pogo *pogo = data;
	int connected;
	unsigned long delay = 0;

	pm_wakeup_event(&pogo->client->dev, 1000);
	connected = gpiod_get_value_cansleep(pogo->connected);
	if (connected > 0)
		atomic64_inc(&pogo->connection_irq_high);
	else {
		atomic64_inc(&pogo->connection_irq_low);
		/*
		 * Samsung's physical-connection ISR only cuts VDDO here.  Holding
		 * NRST asserted also suppresses the STM32's next GPIO62 connection
		 * pulse, leaving the keyboard absent until reset is released.
		 * A disconnect invalidates pending recovery of the old connection.
		 */
		WRITE_ONCE(pogo->reset_on_reconnect, false);
		samsung_pogo_power_off(pogo);
		/*
		 * The STM32 repeats GPIO62 pulses until the host acknowledges them by
		 * dropping VDDO; deferring this cut produced hundreds of edges and no
		 * keys.  Samsung cuts the regulator directly in this IRQ, before the
		 * 250 ms connection check.  Do that before taking the protocol mutex:
		 * application initialization holds the latter across a 200 ms delay,
		 * which otherwise postpones the acknowledgement and makes the MCU
		 * repeat the request until the protocol collapses.
		 */
		mutex_lock(&pogo->lock);
		samsung_pogo_release_keys(pogo);
		mutex_unlock(&pogo->lock);
	}
	if (connected <= 0)
		delay = msecs_to_jiffies(POGO_DISCONNECT_DEBOUNCE_MS);
	mod_delayed_work(system_dfl_wq, &pogo->connection_work, delay);

	return IRQ_HANDLED;
}

static int samsung_pogo_input_event(struct input_dev *input,
				    unsigned int type, unsigned int code, int value)
{
	struct samsung_pogo *pogo = input_get_drvdata(input);

	if (type != EV_LED || code != LED_CAPSL)
		return -EINVAL;

	/* The vendor protocol puts the desired Caps LED state in the next poll. */
	WRITE_ONCE(pogo->caps_request, value ? 2 : 1);
	return 0;
}

static ssize_t diagnostics_show(struct device *dev,
				struct device_attribute *attribute, char *buf)
{
	struct samsung_pogo *pogo = dev_get_drvdata(dev);

	/*
	 * flash_version is the one field that separates a controller this
	 * driver can talk to from one it cannot: the mainline sequence only
	 * drives the V37 application.  Exposing it here means the check costs
	 * a cat instead of grepping a boot-time dmesg line that rotates away.
	 */
	return sysfs_emit(buf,
		"driver_revision=5 attached=%u powered=%u connected=%d data_ready=%d data_irq_enabled=%u reset_pending=%u model=%#04x caps=%u data_irq=%lld data_irq_deasserted=%lld connection_high=%lld connection_low=%lld manual_polls=%lld key_events=%lld keys_down=%u recoveries=%lld read_retry_releases=%lld event_read_errors=%lld last_event_error=%d recovery_gate_skips=%lld invalid_key_events=%lld bootloader=%u flash_version=%*phN app_version=%*phN init_attempts=%lld last_init_error=%d input=%u touchpad=%u touchpad_packets=%lld touchpad_bad_packets=%lld\n",
		READ_ONCE(pogo->attached), READ_ONCE(pogo->powered),
		gpiod_get_value_cansleep(pogo->connected),
		gpiod_get_value_cansleep(pogo->data_ready),
		READ_ONCE(pogo->data_irq_enabled),
		READ_ONCE(pogo->reset_on_reconnect), READ_ONCE(pogo->model),
		READ_ONCE(pogo->caps_request),
		atomic64_read(&pogo->data_irq_count),
		atomic64_read(&pogo->data_irq_deasserted),
		atomic64_read(&pogo->connection_irq_high),
		atomic64_read(&pogo->connection_irq_low),
		atomic64_read(&pogo->manual_poll_count),
		atomic64_read(&pogo->key_event_count),
		bitmap_weight(pogo->keys_down, KEY_MAX + 1),
		atomic64_read(&pogo->recovery_count),
		atomic64_read(&pogo->read_retry_release_count),
		atomic64_read(&pogo->event_read_errors),
		READ_ONCE(pogo->last_event_error),
		atomic64_read(&pogo->recovery_gate_skips),
		atomic64_read(&pogo->invalid_key_events),
		pogo->bootloader_reachable,
		(int)sizeof(pogo->flash_version), pogo->flash_version,
		(int)sizeof(pogo->app_version), pogo->app_version,
		atomic64_read(&pogo->init_attempts), READ_ONCE(pogo->last_init_error),
		READ_ONCE(pogo->input) != NULL, pogo->touchpad != NULL,
		atomic64_read(&pogo->touchpad_packets),
		atomic64_read(&pogo->touchpad_bad_packets));
}
static DEVICE_ATTR_RO(diagnostics);

static ssize_t event_poll_store(struct device *dev,
				struct device_attribute *attribute,
				const char *buf, size_t count)
{
	struct samsung_pogo *pogo = dev_get_drvdata(dev);
	int ret;

	if (!sysfs_streq(buf, "1"))
		return -EINVAL;

	pm_stay_awake(dev);
	mutex_lock(&pogo->lock);
	if (!pogo->attached) {
		ret = -ENODEV;
	} else {
		atomic64_inc(&pogo->manual_poll_count);
		ret = samsung_pogo_read_event(pogo);
	}
	mutex_unlock(&pogo->lock);
	pm_relax(dev);

	return ret ? ret : count;
}
static DEVICE_ATTR_WO(event_poll);

static int samsung_pogo_probe(struct i2c_client *client)
{
	struct device *dev = &client->dev;
	struct device_node *booster_np;
	struct samsung_pogo *pogo;
	int ret;

	if (!i2c_check_functionality(client->adapter, I2C_FUNC_I2C))
		return -EOPNOTSUPP;

	pogo = devm_kzalloc(dev, sizeof(*pogo), GFP_KERNEL);
	if (!pogo)
		return -ENOMEM;

	pogo->client = client;
	pogo->caps_request = 1;
	atomic64_set(&pogo->data_irq_count, 0);
	atomic64_set(&pogo->data_irq_deasserted, 0);
	atomic64_set(&pogo->connection_irq_high, 0);
	atomic64_set(&pogo->connection_irq_low, 0);
	atomic64_set(&pogo->manual_poll_count, 0);
	atomic64_set(&pogo->key_event_count, 0);
	atomic64_set(&pogo->recovery_count, 0);
	atomic64_set(&pogo->read_retry_release_count, 0);
	atomic64_set(&pogo->event_read_errors, 0);
	atomic64_set(&pogo->recovery_gate_skips, 0);
	atomic64_set(&pogo->invalid_key_events, 0);
	pogo->last_event_error = 0;
	mutex_init(&pogo->lock);
	mutex_init(&pogo->recovery_lock);
	mutex_init(&pogo->power_lock);
	INIT_DELAYED_WORK(&pogo->connection_work, samsung_pogo_connection_work);
	INIT_DELAYED_WORK(&pogo->application_work, samsung_pogo_application_work);
	i2c_set_clientdata(client, pogo);

	pogo->vddo = devm_regulator_get(dev, "vddo");
	if (IS_ERR(pogo->vddo))
		return dev_err_probe(dev, PTR_ERR(pogo->vddo), "failed to get VDDO\n");
	ret = devm_add_action_or_reset(dev, samsung_pogo_power_off, pogo);
	if (ret)
		return ret;

	booster_np = of_parse_phandle(dev->of_node, "booster", 0);
	if (!booster_np &&
	    !of_device_is_compatible(dev->of_node,
				     "samsung,gts9pwifi-stm32-pogo"))
		return dev_err_probe(dev, -EINVAL, "missing MAX77816 phandle\n");
	if (booster_np) {
		pogo->booster = of_find_i2c_device_by_node(booster_np);
		of_node_put(booster_np);
		if (!pogo->booster)
			return dev_err_probe(dev, -EPROBE_DEFER,
					     "MAX77816 is not ready\n");
		ret = devm_add_action_or_reset(dev, samsung_pogo_put_booster,
					       &pogo->booster->dev);
		if (ret)
			return ret;
		if (!i2c_check_functionality(pogo->booster->adapter, I2C_FUNC_I2C))
			return dev_err_probe(dev, -EOPNOTSUPP,
					     "MAX77816 adapter lacks raw I2C\n");
	} else {
		dev_info(dev, "no keyboard booster described; using VDDO only\n");
	}

	pogo->boot = devm_gpiod_get(dev, "boot", GPIOD_OUT_LOW);
	if (IS_ERR(pogo->boot))
		return dev_err_probe(dev, PTR_ERR(pogo->boot), "failed to get BOOT0\n");

	/* reset is active-low in DT; logical low means deasserted/high on the pin. */
	pogo->reset = devm_gpiod_get(dev, "reset", GPIOD_OUT_LOW);
	if (IS_ERR(pogo->reset))
		return dev_err_probe(dev, PTR_ERR(pogo->reset), "failed to get reset\n");

	samsung_pogo_probe_bootloader(pogo);

	pogo->data_ready = devm_gpiod_get(dev, "data-ready", GPIOD_IN);
	if (IS_ERR(pogo->data_ready))
		return dev_err_probe(dev, PTR_ERR(pogo->data_ready),
				     "failed to get data-ready GPIO\n");

	pogo->connected = devm_gpiod_get(dev, "connected", GPIOD_IN);
	if (IS_ERR(pogo->connected))
		return dev_err_probe(dev, PTR_ERR(pogo->connected),
				     "failed to get connection GPIO\n");

	pogo->data_irq = gpiod_to_irq(pogo->data_ready);
	if (pogo->data_irq < 0)
		return dev_err_probe(dev, pogo->data_irq,
				     "failed to map data-ready IRQ\n");

	ret = devm_request_threaded_irq(dev, pogo->data_irq,
					samsung_pogo_irq,
					samsung_pogo_irq_thread,
					/*
					 * Samsung wires DATA as level-low + ONESHOT.  This
					 * retriggers after one packet while more events keep
					 * DATA asserted, draining press/release bursts instead
					 * of losing every packet after the first falling edge.
					 * The hard handler preserves short pulses by deciding
					 * validity before the threaded I2C reader is scheduled.
					 */
					IRQF_TRIGGER_LOW | IRQF_ONESHOT,
					dev_name(dev), pogo);
	if (ret)
		return dev_err_probe(dev, ret, "failed to request data-ready IRQ\n");
	disable_irq(pogo->data_irq);
	pogo->data_irq_enabled = false;

	pogo->connection_irq = gpiod_to_irq(pogo->connected);
	if (pogo->connection_irq < 0)
		return dev_err_probe(dev, pogo->connection_irq,
				     "failed to map connection IRQ\n");
	ret = devm_request_threaded_irq(dev, pogo->connection_irq, NULL,
					samsung_pogo_connection_irq_thread,
					IRQF_TRIGGER_RISING | IRQF_TRIGGER_FALLING |
					IRQF_ONESHOT,
					"samsung-pogo-connection", pogo);
	if (ret)
		return dev_err_probe(dev, ret, "failed to request connection IRQ\n");

	device_init_wakeup(dev, pogo_connection_wakeup &&
			   device_property_read_bool(dev, "wakeup-source"));
	if (device_may_wakeup(dev)) {
		ret = enable_irq_wake(pogo->connection_irq);
		if (ret) {
			dev_warn(dev, "connection IRQ cannot wake the tablet: %d\n",
				 ret);
		} else {
			pogo->wake_enabled = true;
		}
	}
	/* The X810 firmware image/version is not interchangeable by assumption. */
	if (pogo->booster) {
		ret = device_create_file(dev, &dev_attr_firmware_update);
		if (ret)
			return dev_err_probe(dev, ret,
					     "failed to create firmware update control\n");
	}
	ret = device_create_file(dev, &dev_attr_diagnostics);
	if (ret)
		return dev_err_probe(dev, ret,
				     "failed to create diagnostics\n");
	ret = device_create_file(dev, &dev_attr_event_poll);
	if (ret)
		return dev_err_probe(dev, ret,
				     "failed to create event poll control\n");
	ret = device_create_file(dev, &dev_attr_recover);
	if (ret)
		return dev_err_probe(dev, ret,
				     "failed to create controller recovery control\n");
	dev_info(dev, "STM32 pogo controller ready (connected=%d, data-ready=%d)\n",
		 gpiod_get_value_cansleep(pogo->connected),
		 gpiod_get_value_cansleep(pogo->data_ready));

	/* Register the input device only if the physical connection is present. */
	mod_delayed_work(system_dfl_wq, &pogo->connection_work, 0);

	return 0;
}

static void samsung_pogo_remove(struct i2c_client *client)
{
	struct samsung_pogo *pogo = i2c_get_clientdata(client);

	if (pogo->booster)
		device_remove_file(&client->dev, &dev_attr_firmware_update);
	device_remove_file(&client->dev, &dev_attr_recover);
	device_remove_file(&client->dev, &dev_attr_diagnostics);
	device_remove_file(&client->dev, &dev_attr_event_poll);
	cancel_delayed_work_sync(&pogo->connection_work);
	cancel_delayed_work_sync(&pogo->application_work);
	if (pogo->wake_enabled)
		disable_irq_wake(pogo->connection_irq);
	samsung_pogo_set_data_irq(pogo, false);
	mutex_lock(&pogo->lock);
	samsung_pogo_unregister_input(pogo);
	samsung_pogo_power_off(pogo);
	mutex_unlock(&pogo->lock);
	device_init_wakeup(&client->dev, false);
}

static const struct of_device_id samsung_pogo_of_match[] = {
	{ .compatible = "samsung,gts9u-stm32-pogo" },
	{ .compatible = "samsung,gts9pwifi-stm32-pogo" },
	{ }
};
MODULE_DEVICE_TABLE(of, samsung_pogo_of_match);

static struct i2c_driver samsung_pogo_driver = {
	.driver = {
		.name = "samsung-gts9u-stm32-pogo",
		.of_match_table = samsung_pogo_of_match,
	},
	.probe = samsung_pogo_probe,
	.remove = samsung_pogo_remove,
};
module_i2c_driver(samsung_pogo_driver);

MODULE_DESCRIPTION("Samsung Galaxy Tab S9 pogo STM32 keyboard controller");
MODULE_AUTHOR("Samsung Electronics; mainline gts9 port adaptation");
MODULE_LICENSE("GPL");
