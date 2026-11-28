#include "desk_volume_keys.h"

#include <algorithm>

#include "application.h"
#include "audio_codec.h"
#include "board.h"
#include "button.h"
#include "display.h"
#include "lvgl.h"

namespace {
lv_obj_t* volume_badge = nullptr;
lv_timer_t* volume_timer = nullptr;
bool up_held = false;
bool down_held = false;

void ShowVolume(int volume) {
    auto* display = Board::GetInstance().GetDisplay();
    if (!display || !display->IsSetupUICalled())
        return;
    DisplayLockGuard lock(display);
    if (!lock)
        return;
    if (!volume_badge) {
        volume_badge = lv_label_create(lv_layer_top());
        lv_obj_set_style_text_font(volume_badge, LV_FONT_DEFAULT, 0);
        lv_obj_set_style_text_color(volume_badge, lv_color_white(), 0);
        lv_obj_set_style_bg_color(volume_badge, lv_color_hex(0x302936), 0);
        lv_obj_set_style_bg_opa(volume_badge, LV_OPA_90, 0);
        lv_obj_set_style_radius(volume_badge, 8, 0);
        lv_obj_set_style_pad_hor(volume_badge, 8, 0);
        lv_obj_set_style_pad_ver(volume_badge, 4, 0);
        lv_obj_align(volume_badge, LV_ALIGN_TOP_MID, 0, 4);
    }
    if (!volume_timer) {
        volume_timer = lv_timer_create(
            [](lv_timer_t* timer) {
                auto* badge = static_cast<lv_obj_t*>(lv_timer_get_user_data(timer));
                lv_obj_add_flag(badge, LV_OBJ_FLAG_HIDDEN);
                lv_timer_pause(timer);
            },
            1500, volume_badge);
        if (!volume_timer) {
            lv_obj_add_flag(volume_badge, LV_OBJ_FLAG_HIDDEN);
            return;
        }
    }
    lv_label_set_text_fmt(volume_badge, "%s  %d%%", volume ? LV_SYMBOL_VOLUME_MAX : LV_SYMBOL_MUTE,
                          volume);
    lv_obj_remove_flag(volume_badge, LV_OBJ_FLAG_HIDDEN);
    lv_timer_reset(volume_timer);
    lv_timer_resume(volume_timer);
}

void ChangeVolume(int step, bool to_limit) {
    Application::GetInstance().Schedule([step, to_limit]() {
        auto* codec = Board::GetInstance().GetAudioCodec();
        int volume =
            to_limit ? (step > 0 ? 100 : 0) : std::clamp(codec->output_volume() + step, 0, 100);
        if (volume != codec->output_volume())
            codec->SetOutputVolume(volume);
        ShowVolume(volume);
    });
}

void BindVolumeKey(Button& button, int step, bool& held) {
    button.OnLongPress([step, &held]() {
        held = true;
        ChangeVolume(step, true);
    });
    button.OnPressUp([step, &held]() {
        if (!held)
            ChangeVolume(step, false);
        held = false;
    });
}

}  // namespace

void BindDeskVolumeKeys(Button& up, Button& down) {
    BindVolumeKey(up, 10, up_held);
    BindVolumeKey(down, -10, down_held);
}
