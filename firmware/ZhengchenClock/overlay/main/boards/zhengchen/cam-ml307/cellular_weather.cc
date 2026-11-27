#include "cellular_weather.h"
#include "http_client.h"
#include <esp_log.h>

bool DownloadCellularWeather(NetworkInterface* network, const char* source, const char* url,
                             std::string& body, size_t limit) {
    constexpr char tag[] = "DeskWeather";
    if (!network) {
        ESP_LOGW(tag, "%s: modem network unavailable", source);
        return false;
    }
    // Keep the weather connection separate from voice/MQTT, UDP and image uploads
    // (slots 0..3). Let the official HTTP parser handle chunked modem responses.
    HttpClient client(network, 4);
    client.SetTimeout(12000);
    client.SetHeader("Accept-Encoding", "identity");
    if (auto open = client.Open("GET", url); !open) {
        ESP_LOGW(tag, "%s: 4G open failed: %s", source, open.error().ToString().c_str());
        return false;
    }
    auto status = client.GetStatusCode();
    if (!status || *status != 200) {
        ESP_LOGW(tag, "%s: 4G HTTP status %d", source, status.value_or(0));
        return false;
    }
    auto length = client.GetBodyLength();
    if (length > limit) {
        ESP_LOGW(tag, "%s: 4G response too large", source);
        return false;
    }
    char chunk[512];
    for (;;) {
        auto count = client.Read(chunk, sizeof(chunk));
        if (!count || *count < 0 || body.size() + *count > limit) {
            ESP_LOGW(tag, "%s: 4G read failed after %u bytes", source, (unsigned)body.size());
            return false;
        }
        if (*count == 0) return !body.empty() && (length == 0 || body.size() == length);
        body.append(chunk, *count);
    }
}
