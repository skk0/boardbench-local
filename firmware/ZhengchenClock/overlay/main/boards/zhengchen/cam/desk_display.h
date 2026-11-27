#pragma once
#include "lcd_display.h"
#include "desk_face.h"

class DeskDisplay final : public SpiLcdDisplay {
public:
    using SpiLcdDisplay::SpiLcdDisplay;
    using SpiLcdDisplay::ShowNotification;
    void SetupUI() override;
    void SetTheme(Theme* theme) override;
    void UpdateStatusBar(bool all=false) override;
    void SetChatMessage(const char* role, const char* text) override;
    void RemoveChatMessage(const char* role, const char* text) override;
    void ClearChatMessages() override;
    void SetHideSubtitle(bool hide) override;
    void SetPreviewImage(std::unique_ptr<LvglImage> image) override;
    void ShowNotification(const char* text,int duration=3000) override;
private:
    DeskFace face_;
    bool has_system_message_ = false;
};
