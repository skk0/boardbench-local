#pragma once

#include <cstddef>
#include <string>

class NetworkInterface;
bool DownloadCellularWeather(NetworkInterface* network, const char* source, const char* url,
                             std::string& body, size_t limit);
