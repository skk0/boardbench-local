#pragma once

#include <string>

struct DeskWeather {
    std::string description;
    int code = -1;
    int current = 0, low = 0, high = 0;
    bool valid = false;
    std::string city;
};

void StartDeskWeather();
void DeskWeatherNetworkChanged(bool has_ip);
DeskWeather ReadDeskWeather();
void SetDeskLocation(int latitude100, int longitude100);
