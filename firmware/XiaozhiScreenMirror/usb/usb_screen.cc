// Opt-in for boards whose primary console RX is dedicated to the USB serial bridge.
#include "board.h"
#include "display.h"
#include <esp_rom_crc.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <fcntl.h>
#include <unistd.h>
#include <cstdio>
#include <cstdarg>
#include <cstring>
#include <string>

namespace {
constexpr size_t kLimit = 65536;
constexpr char kHex[] = "0123456789abcdef";
#if CONFIG_LV_USE_SNAPSHOT
constexpr bool kSnapshot = true;
#else
constexpr bool kSnapshot = false;
#endif
void Line(int fd, const char* format, ...) {
    char output[320];
    va_list args;
    va_start(args, format);
    int n = vsnprintf(output, sizeof(output), format, args);
    va_end(args);
    if (n > 0 && static_cast<size_t>(n) < sizeof(output)) write(fd, output, n);
}
void Capture(int fd, Display* display, const char* id) {
    std::string jpeg;
    if (!display->SnapshotToJpeg(jpeg, 55) || jpeg.empty() || jpeg.size() > kLimit) {
        Line(fd, "BBS1 ERR %s capture\n", id);
        return;
    }
    auto* bytes = reinterpret_cast<const uint8_t*>(jpeg.data());
    const auto crc = esp_rom_crc32_le(0, bytes, jpeg.size());
    for (size_t pos = 0; pos < jpeg.size();) {
        char hex[257];
        size_t n = jpeg.size() - pos;
        if (n > 128) n = 128;
        for (size_t i = 0; i < n; ++i) {
            hex[2*i] = kHex[bytes[pos+i] >> 4];
            hex[2*i+1] = kHex[bytes[pos+i] & 15];
        }
        hex[2*n] = 0;
        Line(fd, "BBS1 DATA %s %u %s\n", id, unsigned(pos), hex);
        pos += n;
        vTaskDelay(1);
    }
    Line(fd, "BBS1 END %s %u %08x\n", id, unsigned(jpeg.size()), unsigned(crc));
}

void Serve(void*) {
    int fd = -1;
#if CONFIG_ESP_CONSOLE_UART
    char path[24];
    snprintf(path, sizeof(path), "/dev/uart/%d", CONFIG_ESP_CONSOLE_UART_NUM);
    fd = open(path, O_RDWR | O_NONBLOCK);
#elif CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
    fd = open("/dev/usbserjtag", O_RDWR | O_NONBLOCK);
#endif
    if (fd < 0) { vTaskDelete(nullptr); return; }
    auto* display = Board::GetInstance().GetDisplay();
    const bool capable = display && display->SupportsGuiOperations() && kSnapshot;
    int64_t announce = 0, last = -2000000;
    char line[40], buf[64];
    size_t used = 0;
    bool discard = false;
    while (true) {
        auto now = esp_timer_get_time();
        if (now >= announce) {
            Line(fd, "BBS1 CAP %s\n", capable ? "JPEG" : "NO_CAPTURE");
            announce = now + 4000000;
        }
        int n = read(fd, buf, sizeof(buf));
        for (int i=0; i<n; ++i) {
            const char c = buf[i];
            if (c == '\r') continue;
            if (c != '\n') {
                if (used < sizeof(line)-1) line[used++] = c;
                else discard = true;
                continue;
            }
            line[used] = 0;
            if (!discard && capable && used == 17 && !strncmp(line, "BBS1 GET ", 9) &&
                strspn(line+9, kHex) == 8 && now-last >= 1000000) {
                last = now;
                Capture(fd, display, line+9);
            }
            used = 0;
            discard = false;
        }
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}
}
void StartUsbScreen() { xTaskCreate(Serve, "usb_screen", 8192, nullptr, 2, nullptr); }
