#include "desk_display.h"
#include "application.h"
#include "board.h"
#include "lvgl_theme.h"
#include <cstring>

extern const uint8_t desk_pixels[] asm("_binary_desk_wall_rgb565_start");

void DeskDisplay::SetupUI() {
    if (IsSetupUICalled()) return;
    SpiLcdDisplay::SetupUI();
    DisplayLockGuard lock(this);
    if (!lock) return;
    auto theme=static_cast<LvglTheme*>(current_theme_);
    face_.Create(lv_screen_active(),theme->text_font()->font(),theme->icon_font()->font(),desk_pixels);
    StartDeskWeather();
}
void DeskDisplay::SetTheme(Theme* theme) {
    SpiLcdDisplay::SetTheme(theme);
    DisplayLockGuard lock(this);
    if(lock && theme) face_.Font(static_cast<LvglTheme*>(theme)->text_font()->font());
}
void DeskDisplay::UpdateStatusBar(bool all) {
    SpiLcdDisplay::UpdateStatusBar(all);
    auto state=Application::GetInstance().GetDeviceState();
    bool normal=state==kDeviceStateIdle || state==kDeviceStateListening || state==kDeviceStateSpeaking;
    DisplayLockGuard lock(this);
    if(!lock) return;
    auto shown=[](lv_obj_t* o){return o && !lv_obj_has_flag(o,LV_OBJ_FLAG_HIDDEN);};
    bool show=normal && !has_system_message_ && !shown(notification_label_) && !shown(low_battery_popup_) && !shown(preview_image_);
    face_.Visible(show);
    if(!show) return;
    face_.Status(state==kDeviceStateListening ? "正在听" : state==kDeviceStateSpeaking ? "回答中" : "");
    int level=-1; bool charging,discharging;
    if(!Board::GetInstance().GetBatteryLevel(level,charging,discharging)) level=-1;
    face_.Update(time(nullptr),ReadDeskWeather(),network_icon_,battery_icon_,level);
}
void DeskDisplay::SetChatMessage(const char* role,const char* text) {
    SpiLcdDisplay::SetChatMessage(role,text);
    DisplayLockGuard lock(this);
    if (!lock) return;
    if (!strcmp(role,"system")) {
        has_system_message_ = text && *text;
        if(has_system_message_) face_.Visible(false);
    } else face_.Say(role,text);
}
void DeskDisplay::RemoveChatMessage(const char* role,const char* text) {
    SpiLcdDisplay::RemoveChatMessage(role,text);
    DisplayLockGuard lock(this); if(lock) face_.Remove(role,text);
}
void DeskDisplay::ClearChatMessages() {
    SpiLcdDisplay::ClearChatMessages();
    DisplayLockGuard lock(this);
    if(lock) {
        // Upstream clears transient subtitles on entering idle. The clock keeps
        // the last three conversation bubbles in RAM for its standby view.
        if(Application::GetInstance().GetDeviceState()!=kDeviceStateIdle) face_.Clear();
        has_system_message_=false;
    }
}
void DeskDisplay::SetHideSubtitle(bool hide) {
    SpiLcdDisplay::SetHideSubtitle(hide);
    DisplayLockGuard lock(this); if(lock) face_.Subtitles(!hide);
}
void DeskDisplay::SetPreviewImage(std::unique_ptr<LvglImage> image) {
    if (image) { DisplayLockGuard lock(this); if(lock) face_.Visible(false); }
    SpiLcdDisplay::SetPreviewImage(std::move(image));
}
void DeskDisplay::ShowNotification(const char* text,int duration) {
    { DisplayLockGuard lock(this); if(lock) face_.Visible(false); }
    SpiLcdDisplay::ShowNotification(text,duration);
}
