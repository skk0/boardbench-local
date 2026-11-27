#pragma once
#include <lvgl.h>
#include <ctime>
#include <string>
#include "desk_weather.h"

class DeskFace {
public:
    void Create(lv_obj_t* screen, const lv_font_t* body, const lv_font_t* icons, const uint8_t* pixels);
    void Font(const lv_font_t* font);
    void Update(time_t now, const DeskWeather& weather, const char* network, const char* battery, int percent);
    void Say(const char* role, const char* text);
    void Clear();
    void Status(const char* text);
    void Remove(const char* role, const char* text);
    void Visible(bool visible);
    void Subtitles(bool visible);
private:
    lv_image_dsc_t image_{};
    lv_obj_t *root_{}, *clock_{}, *date_{}, *condition_{}, *temp_{}, *range_{}, *sky_{}, *sun_{}, *cloud_{};
    lv_obj_t *network_{}, *battery_{}, *percent_{}, *history_{}, *state_{}, *city_{};
};
