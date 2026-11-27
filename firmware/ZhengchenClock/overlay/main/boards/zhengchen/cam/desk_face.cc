#include "desk_face.h"
#include <cstdio>
#include <cstring>

LV_FONT_DECLARE(desk_digits);
namespace {
constexpr uint32_t ink = 0x302936;
lv_obj_t* Box(lv_obj_t* parent, int x, int y, int w, int h) {
    auto o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_remove_flag(o,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_pos(o,x,y); lv_obj_set_size(o,w,h);
    return o;
}
lv_obj_t* Label(lv_obj_t* parent, int x, int y, const lv_font_t* font = nullptr) {
    auto o=lv_label_create(parent);
    lv_label_set_text(o,"");
    lv_obj_set_pos(o,x,y);
    if (font) lv_obj_set_style_text_font(o,font,0);
    lv_obj_set_style_text_color(o,lv_color_hex(ink),0);
    return o;
}
void Fill(lv_obj_t* o,uint32_t color,int radius,int opacity=255) {
    lv_obj_set_style_bg_color(o,lv_color_hex(color),0);
    lv_obj_set_style_bg_opa(o,opacity,0);
    lv_obj_set_style_radius(o,radius,0);
}
void Text(lv_obj_t* label,const char* text) {
    if (strcmp(lv_label_get_text(label),text)) lv_label_set_text(label,text);
}
void Show(lv_obj_t* obj,bool show) {
    if (show) lv_obj_remove_flag(obj,LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(obj,LV_OBJ_FLAG_HIDDEN);
}
}
void DeskFace::Create(lv_obj_t* screen,const lv_font_t* body,const lv_font_t* icons,const uint8_t* pixels) {
    root_=Box(screen,0,0,320,240);
    Font(body);
    image_.header.magic=LV_IMAGE_HEADER_MAGIC; image_.header.cf=LV_COLOR_FORMAT_RGB565;
    image_.header.w=320; image_.header.h=240; image_.header.stride=640;
    image_.data_size=320*240*2; image_.data=pixels;
    auto bg=lv_image_create(root_); lv_image_set_src(bg,&image_);
    network_=Label(root_,8,1,icons);
    state_=Label(root_,38,1);
    city_=Label(root_,101,2);
    lv_obj_set_size(city_,148,20);
    lv_label_set_long_mode(city_,LV_LABEL_LONG_MODE_DOTS);
    battery_=Label(root_,266,1,icons);
    percent_=Label(root_,286,3); Fill(percent_,0xfff7ef,4,205);
    clock_=Label(root_,7,28,&desk_digits);
    date_=Label(root_,9,66);
    sky_=Box(root_,10,93,26,18);
    sun_=Box(sky_,1,0,13,13); Fill(sun_,0xf6ae35,7);
    cloud_=Box(sky_,8,5,17,11); Fill(cloud_,0xe1dced,7);
    condition_=Label(root_,40,88);
    temp_=Label(root_,7,110,&desk_digits);
    range_=Label(root_,82,125);
    history_=Box(root_,7,150,306,88);
    lv_obj_set_flex_flow(history_,LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(history_,3,0);
    lv_obj_add_flag(history_,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scroll_dir(history_,LV_DIR_VER);
    lv_obj_set_scrollbar_mode(history_,LV_SCROLLBAR_MODE_OFF);
    Visible(false);
}
void DeskFace::Font(const lv_font_t* font) {
    if(root_ && font) lv_obj_set_style_text_font(root_,font,0);
}
void DeskFace::Visible(bool yes) { if(root_) Show(root_,yes); }
void DeskFace::Subtitles(bool yes) { if(history_) Show(history_,yes); }
void DeskFace::Status(const char* text) { if(state_) Text(state_,text); }
void DeskFace::Remove(const char* role,const char* text) {
    if (!history_ || !role || !text) return;
    std::string line=std::string(!strcmp(role,"user") ? "你  " : "小智  ")+text;
    for(int i=(int)lv_obj_get_child_count(history_)-1;i>=0;--i) {
        auto row=lv_obj_get_child(history_,i);
        if(line==lv_label_get_text(row)) { lv_obj_delete(row); return; }
    }
}
void DeskFace::Clear() { if(history_) lv_obj_clean(history_); }
void DeskFace::Say(const char* role,const char* text) {
    if(!history_ || !text || !*text || (strcmp(role,"assistant") && strcmp(role,"user"))) return;
    if(lv_obj_get_child_count(history_)>=3) lv_obj_delete(lv_obj_get_child(history_,0));
    bool mine=strcmp(role,"user")==0;
    auto row=Label(history_,0,0);
    lv_obj_set_style_max_width(row,302,0);
    lv_label_set_long_mode(row,LV_LABEL_LONG_WRAP);
    Fill(row,mine?0xfffbfe:0xffe4ed,7,235);
    lv_obj_set_style_pad_hor(row,6,0); lv_obj_set_style_pad_ver(row,1,0);
    lv_label_set_text_fmt(row,"%s  %s",mine?"你":"小智",text);
    lv_obj_update_layout(history_);
    lv_obj_scroll_to_y(history_,LV_COORD_MAX,LV_ANIM_OFF);
}
void DeskFace::Update(time_t now,const DeskWeather& w,const char* net,const char* bat,int pct) {
    if(!root_) return;
    struct tm local{}; localtime_r(&now,&local);
    char buf[64];
    if(local.tm_year>=125) {
        strftime(buf,sizeof(buf),"%H:%M",&local); Text(clock_,buf);
        static const char* days[]={"日","一","二","三","四","五","六"};
        snprintf(buf,sizeof(buf),"%d月%d日 星期%s",local.tm_mon+1,local.tm_mday,days[local.tm_wday]); Text(date_,buf);
    } else { Text(clock_,"--:--"); Text(date_,"时间同步中"); }
    Text(network_,net?net:""); Text(battery_,bat?bat:"");
    if(pct>=0) snprintf(buf,sizeof(buf),"%d%%",pct); else buf[0]=0;
    Text(percent_,buf);
    Text(city_,w.city.c_str());
    Text(condition_,w.description.c_str()); Show(sky_,w.valid && w.code >= 0);
    lv_obj_set_x(condition_, w.valid && w.code >= 0 ? 40 : 9);
    Show(sun_,w.code>=0 && w.code<=2); Show(cloud_,w.code!=0);
    lv_obj_set_style_bg_color(cloud_,lv_color_hex(w.code>=45?0x8095b7:0xe1dced),0);
    if(w.valid) {
        snprintf(buf,sizeof(buf),"%d°",w.current); Text(temp_,buf);
        snprintf(buf,sizeof(buf),"%d° / %d°",w.low,w.high); Text(range_,buf);
    } else { Text(temp_,"--°"); Text(range_,""); }
}
