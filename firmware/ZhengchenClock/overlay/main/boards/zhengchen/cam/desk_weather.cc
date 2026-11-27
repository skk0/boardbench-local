#include "desk_weather_data.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#if CONFIG_BOARD_TYPE_ZHENGCHEN_CAM_ML307
#include "../cam-ml307/cellular_weather.h"
#include "dual_network_board.h"
#endif
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <cstdio>
#include <mutex>

namespace {
constexpr char kTag[] = "DeskWeather";
constexpr char kShenzhen[] = "2254,11406";
constexpr int64_t kMinute = 60000000;
std::mutex guard;
TaskHandle_t worker = nullptr;
WeatherPlace location;
std::string displayed_city;
bool online = false;
uint32_t generation = 0;
DeskWeather reading;
int64_t updated = 0;
const char* status = "等待联网";

bool Download(const char* source, const char* url, std::string& body, size_t limit = 8192) {
#if CONFIG_BOARD_TYPE_ZHENGCHEN_CAM_ML307
    auto& board = static_cast<DualNetworkBoard&>(Board::GetInstance());
    if (board.GetNetworkType() == NetworkType::ML307) {
        return DownloadCellularWeather(board.GetNetwork(), source, url, body, limit);
    }
#endif
    esp_http_client_config_t config{};
    config.url = url;
    config.timeout_ms = 8000;
    config.crt_bundle_attach = esp_crt_bundle_attach;
    auto client = esp_http_client_init(&config);
    if (!client) {
        ESP_LOGW(kTag, "%s: cannot allocate HTTPS client", source);
        return false;
    }
    esp_http_client_set_header(client, "Accept-Encoding", "identity");
    auto err = esp_http_client_open(client, 0);
    bool ok = false;
    if (err != ESP_OK) {
        ESP_LOGW(kTag, "%s: HTTPS open failed: %s, errno=%d", source,
                 esp_err_to_name(err), esp_http_client_get_errno(client));
    } else {
        auto length = esp_http_client_fetch_headers(client);
        int http = esp_http_client_get_status_code(client);
        if (length >= 0 && static_cast<size_t>(length) <= limit && http == 200) {
            char chunk[512];
            int count;
            while ((count = esp_http_client_read(client, chunk, sizeof(chunk))) > 0 &&
                   body.size() + count <= limit) body.append(chunk, count);
            ok = count == 0 && !body.empty() && esp_http_client_is_complete_data_received(client);
        }
        if (!ok) ESP_LOGW(kTag, "%s: HTTP %d, length=%lld, bytes=%u, errno=%d", source,
                           http, (long long)length, (unsigned)body.size(), esp_http_client_get_errno(client));
    }
    esp_http_client_cleanup(client);
    return ok;
}

bool Locate(WeatherPlace& place) {
    std::string body;
    if (Download("IP9", "https://ip9.com.cn/get", body) && ParseIpPlace(body.c_str(), true, place))
        return true;
    body.clear();
    return Download("IPWho", "https://ipwho.is/?lang=zh-CN&fields=success,city,region,country_code,latitude,longitude", body) &&
           ParseIpPlace(body.c_str(), false, place);
}

void ResolveStation(WeatherPlace& place) {
    if (!place.station.empty()) return;
    auto province = NmcProvince(place.province);
    if (!province) return;
    if (place.city == "深圳" && place.province.find("广东") == 0) {
        place.station = "AhpEU";
        return;
    }
    std::string stations;
    auto url = std::string("https://www.nmc.cn/rest/province/") + province;
    if (Download("NMC stations", url.c_str(), stations, 49152))
        place.station = FindNmcStation(stations.c_str(), place.city);
}

bool Fetch(WeatherPlace& place, DeskWeather& out) {
    ResolveStation(place);
    if (!place.station.empty()) {
        std::string real, predict;
        auto observation = "https://www.nmc.cn/rest/real/" + place.station;
        auto forecast = "https://www.nmc.cn/rest/predict/" + place.station;
        if (Download("NMC real", observation.c_str(), real) &&
            Download("NMC forecast", forecast.c_str(), predict) &&
            ParseNmcWeather(real.c_str(), predict.c_str(), place.city, out)) {
            ESP_LOGI(kTag, "Weather source: NMC");
            return true;
        }
        ESP_LOGW(kTag, "Station weather unavailable; trying Open-Meteo for the same location");
    }
    int lat, lon;
    if (sscanf(place.coordinates.c_str(), "%d,%d", &lat, &lon) != 2) return false;
    char url[320];
    snprintf(url, sizeof(url), "https://api.open-meteo.com/v1/forecast?latitude=%.2f&longitude=%.2f"
             "&current=temperature_2m,weather_code&daily=temperature_2m_min,temperature_2m_max"
             "&forecast_days=1&timezone=auto", lat / 100.0, lon / 100.0);
    std::string body;
    if (!Download("Open-Meteo", url, body)) return false;
    if (ParseOpenMeteoWeather(body.c_str(), out)) return true;
    ESP_LOGW(kTag, "Open-Meteo returned invalid weather data");
    return false;
}

void Worker(void*) {
    TickType_t delay = portMAX_DELAY;
    for (;;) {
        ulTaskNotifyTake(pdTRUE, delay);
        WeatherPlace target;
        uint32_t requested;
        {
            std::lock_guard lock(guard);
            if (!online) {
                delay = portMAX_DELAY;
                continue;
            }
            requested = generation;
            target = location;
            status = target.coordinates.empty() ? "正在定位" : "正在更新";
        }
        bool fallback = false;
        if (target.coordinates.empty()) {
            if (Locate(target)) {
                ESP_LOGI(kTag, "Public IP city: %s", target.city.c_str());
            } else {
                fallback = true;
                target = {"深圳", kShenzhen, "广东", "AhpEU"};
                ESP_LOGW(kTag, "IP location unavailable; using Shenzhen");
            }
        }
        {
            std::lock_guard lock(guard);
            if (requested != generation) { delay = portMAX_DELAY; continue; }
            if (displayed_city != target.city) reading = {};
            displayed_city = target.city;
            status = "正在更新";
        }
        DeskWeather fresh;
        bool ok = Fetch(target, fresh);
        std::lock_guard lock(guard);
        if (requested != generation) {
            delay = portMAX_DELAY;
            continue;
        }
        if (!fallback) location = target;
        fresh.city = target.city;
        if (ok) {
            reading = fresh;
            updated = esp_timer_get_time();
            ESP_LOGI(kTag, "Weather updated: %d C, range %d..%d C",
                     fresh.current, fresh.low, fresh.high);
        } else {
            status = "稍后重试";
            ESP_LOGW(kTag, "Weather refresh failed; retry in 60 s");
        }
        // Retry geolocation sooner when the default city was used.
        unsigned seconds = !ok ? 60 : (fallback ? 300 : 1800);
        delay = pdMS_TO_TICKS(seconds * 1000);
    }
}

void NotifyLocked() {
    ++generation;
    if (worker) xTaskNotifyGive(worker);
}
}

void StartDeskWeather() {
    std::lock_guard lock(guard);
    if (worker) return;
    if (xTaskCreate(Worker, "desk_weather", 8192, nullptr, 2, &worker) != pdPASS) {
        status = "天气未启动";
        ESP_LOGE(kTag, "Cannot start weather worker");
        return;
    }
    if (online) NotifyLocked();
}

void DeskWeatherNetworkChanged(bool has_ip) {
    std::lock_guard lock(guard);
    online = has_ip;
    if (has_ip) {
        location = {};
        reading = {};
        displayed_city.clear();
        ESP_LOGI(kTag, "Network ready; locating weather city");
    }
    status = has_ip ? "正在定位" : "等待联网";
    NotifyLocked();
}

void SetDeskLocation(int latitude100, int longitude100) {
    std::string coords = std::to_string(latitude100) + "," + std::to_string(longitude100);
    std::lock_guard lock(guard);
    location = coords == kShenzhen ? WeatherPlace{"深圳", coords, "广东", "AhpEU"} :
                                     WeatherPlace{"手动地点", coords, "", ""};
    displayed_city = location.city;
    reading = {};
    status = online ? "正在更新" : "等待联网";
    NotifyLocked();
}

DeskWeather ReadDeskWeather() {
    std::lock_guard lock(guard);
    if (reading.valid && esp_timer_get_time() - updated < 120 * kMinute) return reading;
    DeskWeather result;
    result.city = displayed_city;
    result.description = reading.valid && online ? "天气待更新" : status;
    return result;
}
