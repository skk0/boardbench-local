#include "desk_mirror.h"
#include "application.h"
#include "board.h"
#include "display.h"
#include <wifi_manager.h>
#include <esp_http_server.h>
#include <esp_random.h>
#include <esp_timer.h>
#include <esp_log.h>
#include <atomic>
#include <cstdio>
#include <cstring>

namespace {
constexpr int64_t kLifetime = 600000000;  // Ten minutes, in microseconds.
httpd_handle_t server = nullptr;
esp_timer_handle_t timer = nullptr;
std::atomic<bool> enabled{false};
int64_t expires = 0, last_capture = 0;
char credential[24] = {};
unsigned failures = 0;

esp_err_t Reply(httpd_req_t* req, const char* status) {
    httpd_resp_set_status(req, status);
    return httpd_resp_send(req, "", 0);
}

esp_err_t Frame(httpd_req_t* req) {
    auto now = esp_timer_get_time();
    if (!enabled || now >= expires) return Reply(req, "410 Gone");
    char auth[sizeof(credential)];
    if (httpd_req_get_hdr_value_str(req, "Authorization", auth, sizeof(auth)) != ESP_OK ||
        std::strcmp(auth, credential) != 0) {
        if (++failures >= 8) CloseDeskMirror();
        return Reply(req, "403 Forbidden");
    }
    if (now - last_capture < 900000) return Reply(req, "429 Too Many Requests");
    last_capture = now;
    std::string jpeg;
    if (!Board::GetInstance().GetDisplay()->SnapshotToJpeg(jpeg, 65) || jpeg.empty())
        return Reply(req, "503 Service Unavailable");
    httpd_resp_set_type(req, "image/jpeg");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    return httpd_resp_send(req, jpeg.data(), jpeg.size());
}

// Server lifecycle is serialized on the application task. The HTTP task only serves frames.
void Stop() {
    enabled = false;
    if (timer) esp_timer_stop(timer);
    if (server) {
        httpd_stop(server);
        server = nullptr;
    }
}

void Toggle() {
    auto display = Board::GetInstance().GetDisplay();
    if (server) {
        Stop();
        display->ShowNotification("投屏已关闭");
        return;
    }
    auto& wifi = WifiManager::GetInstance();
    if (!wifi.IsInitialized() || !wifi.IsConnected()) {
        display->ShowNotification("投屏需要 Wi-Fi");
        return;
    }
    auto ip = wifi.GetIpAddress();
    if (ip.empty() || ip == "0.0.0.0") return;
    if (!timer) {
        esp_timer_create_args_t args{};
        args.callback = [](void*) { CloseDeskMirror(); };
        args.name = "screen_share";
        if (esp_timer_create(&args, &timer) != ESP_OK) {
            display->ShowNotification("投屏暂不可用");
            return;
        }
    }
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.server_port = 8088;
    cfg.ctrl_port = 32888;
    cfg.max_open_sockets = 2;
    cfg.lru_purge_enable = true;
    cfg.stack_size = 8192;
    cfg.send_wait_timeout = 2;
    cfg.recv_wait_timeout = 2;
    auto code = 100000 + esp_random() % 900000;
    std::snprintf(credential, sizeof(credential), "Bearer %06u", (unsigned)code);
    last_capture = 0;
    failures = 0;
    expires = esp_timer_get_time() + kLifetime;
    httpd_uri_t route{};
    route.uri = "/screen.jpg";
    route.method = HTTP_GET;
    route.handler = Frame;
    if (httpd_start(&server, &cfg) != ESP_OK ||
        httpd_register_uri_handler(server, &route) != ESP_OK ||
        esp_timer_start_once(timer, kLifetime) != ESP_OK) {
        Stop();
        display->ShowNotification("投屏暂不可用");
        return;
    }
    enabled = true;
#ifdef BOARDBENCH_COMPACT_PAIRING
    ESP_LOGI("ScreenMirror", "Screen address: %s:8088", ip.c_str());
    display->ShowNotification(std::to_string(code), 12000);
#else
    display->ShowNotification(ip + "  码 " + std::to_string(code), 12000);
#endif
}
}

void ToggleDeskMirror() { Application::GetInstance().Schedule(Toggle); }

void CloseDeskMirror() {
    if (enabled.exchange(false)) Application::GetInstance().Schedule(Stop);
}
