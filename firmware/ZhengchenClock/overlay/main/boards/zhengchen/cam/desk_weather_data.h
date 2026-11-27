#pragma once
#include "desk_weather.h"

struct WeatherPlace {
    std::string city, coordinates, province, station;
};
bool ParseIpPlace(const char* body, bool ip9, WeatherPlace& out);
const char* NmcProvince(const std::string& province);
std::string FindNmcStation(const char* body, const std::string& city);
bool ParseNmcWeather(const char* observation, const char* forecast, const std::string& city, DeskWeather& out);
bool ParseOpenMeteoWeather(const char* body, DeskWeather& out);
