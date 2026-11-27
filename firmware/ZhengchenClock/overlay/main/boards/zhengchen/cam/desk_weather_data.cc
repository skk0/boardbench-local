#include "desk_weather_data.h"
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <memory>
#include "cJSON.h"

namespace {
using Json = std::unique_ptr<cJSON, decltype(&cJSON_Delete)>;
Json Parse(const char* body) { return Json(cJSON_Parse(body), cJSON_Delete); }
const cJSON* Field(const cJSON* item, const char* key) {
    return cJSON_GetObjectItemCaseSensitive(item, key);
}
const char* String(const cJSON* value) {
    return cJSON_IsString(value) ? value->valuestring : "";
}
bool Temperature(const cJSON* value, int& result) {
    double number;
    if (cJSON_IsNumber(value)) {
        number = value->valuedouble;
    } else {
        const char* text = String(value);
        char* end;
        number = std::strtod(text, &end);
        if (end == text || *end) return false;
    }
    if (!std::isfinite(number) || number < -90 || number > 65) return false;
    result = std::lround(number);
    return true;
}
const char* Condition(int code) {
    if (code == 0) return "晴";
    if (code == 1) return "少云";
    if (code == 2) return "多云";
    if (code == 3) return "阴";
    if (code == 45 || code == 48) return "雾";
    if (code >= 95) return "雷雨";
    if ((code >= 71 && code <= 77) || code == 85 || code == 86) return "雪";
    if (code >= 51 && code <= 82) return "雨";
    return "天气";
}
}

bool ParseOpenMeteoWeather(const char* body, DeskWeather& out) {
    auto root = Parse(body);
    auto current = Field(root.get(), "current");
    auto daily = Field(root.get(), "daily");
    auto code = Field(current, "weather_code");
    if (!cJSON_IsNumber(code) || !Temperature(Field(current, "temperature_2m"), out.current) ||
        !Temperature(cJSON_GetArrayItem(Field(daily, "temperature_2m_min"), 0), out.low) ||
        !Temperature(cJSON_GetArrayItem(Field(daily, "temperature_2m_max"), 0), out.high) ||
        out.low > out.high) return false;
    out.code = code->valueint;
    out.description = Condition(out.code);
    out.valid = true;
    return true;
}

bool ParseNmcWeather(const char* observation, const char* forecast, const std::string& city, DeskWeather& out) {
    auto real = Parse(observation);
    auto predict = Parse(forecast);
    for (const auto* item : {real.get(), predict.get()}) {
        if (city != String(Field(Field(item, "station"), "city"))) return false;
    }
    auto today = cJSON_GetArrayItem(Field(predict.get(), "detail"), 0);
    auto weather = Field(real.get(), "weather");
    auto day = Field(Field(today, "day"), "weather");
    auto night = Field(Field(today, "night"), "weather");
    int a, b;
    if (!Temperature(Field(weather, "temperature"), out.current) ||
        !Temperature(Field(day, "temperature"), a) || !Temperature(Field(night, "temperature"), b)) return false;
    out.low = std::min(a, b);
    out.high = std::max(a, b);
    const char* condition = String(Field(weather, "info"));
    if (*condition && std::strcmp(condition, "-") && std::strcmp(condition, "9999")) {
        out.description = condition;
    } else {
        condition = String(Field(day, "info"));
        out.description = *condition ? std::string("预报") + condition : "当地实况";
    }
    // Use WMO codes for the two pictograms shared by both providers.
    out.code = -1;
    if (std::strcmp(condition, "晴") == 0) out.code = 0;
    else if (std::strcmp(condition, "多云") == 0) out.code = 2;
    else if (std::strcmp(condition, "阴") == 0) out.code = 3;

    out.valid = true;
    return true;
}

namespace {
bool Coordinate(const cJSON* field, int limit, int& rounded) {
    double value;
    if (cJSON_IsNumber(field)) value = field->valuedouble;
    else {
        const char* text = String(field);
        char* end;
        value = std::strtod(text, &end);
        if (text == end || *end) return false;
    }
    if (!std::isfinite(value) || std::abs(value) > limit) return false;
    rounded = std::lround(value * 100);
    return true;
}
}

bool ParseIpPlace(const char* body, bool ip9, WeatherPlace& out) {
    auto root = Parse(body);
    const cJSON* data = root.get();
    if (ip9) {
        auto ret = Field(data, "ret");
        if (!cJSON_IsNumber(ret) || ret->valueint != 200) return false;
        data = Field(data, "data");
    } else if (!cJSON_IsTrue(Field(data, "success"))) return false;
    std::string city = String(Field(data, "city"));
    int lat, lon;
    if (city.empty() || city.size() > 96 || city.find_first_of("\r\n\t") != std::string::npos ||
        !Coordinate(Field(data, ip9 ? "lat" : "latitude"), 90, lat) ||
        !Coordinate(Field(data, ip9 ? "lng" : "longitude"), 180, lon) || (!lat && !lon)) return false;
    std::string province;
    std::string country = String(Field(data, "country_code"));
    if (country == "cn" || country == "CN") {
        province = String(Field(data, ip9 ? "prov" : "region"));
        constexpr char suffix[] = "市";
        if (city.size() > sizeof(suffix)-1 && city.compare(city.size()-sizeof(suffix)+1, sizeof(suffix)-1, suffix)==0)
            city.resize(city.size()-sizeof(suffix)+1);
    }
    out = {city, std::to_string(lat) + "," + std::to_string(lon), province, ""};
    return true;
}

const char* NmcProvince(const std::string& province) {
    // Province identifiers published by the National Meteorological Center.
    static constexpr struct { const char* name; const char* code; } provinces[] = {
        {"北京","ABJ"}, {"天津","ATJ"}, {"河北","AHE"}, {"山西","ASX"},
        {"内蒙古","ANM"}, {"辽宁","ALN"}, {"吉林","AJL"}, {"黑龙江","AHL"},
        {"上海","ASH"}, {"江苏","AJS"}, {"浙江","AZJ"}, {"安徽","AAH"},
        {"福建","AFJ"}, {"江西","AJX"}, {"山东","ASD"}, {"河南","AHA"},
        {"湖北","AHB"}, {"湖南","AHN"}, {"广东","AGD"}, {"广西","AGX"},
        {"海南","AHI"}, {"重庆","ACQ"}, {"四川","ASC"}, {"贵州","AGZ"},
        {"云南","AYN"}, {"西藏","AXZ"}, {"陕西","ASN"}, {"甘肃","AGS"},
        {"青海","AQH"}, {"宁夏","ANX"}, {"新疆","AXJ"}, {"香港","AXG"},
        {"澳门","AAM"}, {"台湾","ATW"},
    };
    for (const auto& p : provinces) {
        if (province.compare(0, std::strlen(p.name), p.name) == 0) return p.code;
    }
    return nullptr;
}

std::string FindNmcStation(const char* body, const std::string& city) {
    // Parse one station at a time to keep peak memory bounded on the ESP32.
    const char* cursor = std::strchr(body, '[');
    if (!cursor) return {};
    ++cursor;
    for (;;) {
        const char* end = nullptr;
        Json item(cJSON_ParseWithOpts(cursor, &end, false), cJSON_Delete);
        if (!item || !end || end <= cursor) return {};
        if (city == String(Field(item.get(), "city"))) {
            std::string code = String(Field(item.get(), "code"));
            if (!code.empty() && code.size() <= 12 &&
                code.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789") == std::string::npos)
                return code;
        }
        cursor = end;
        while (*cursor == ' ' || *cursor == '\t' || *cursor == '\n' || *cursor == '\r') ++cursor;
        if (*cursor++ != ',') return {};
    }
}
