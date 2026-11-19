#include "cctvlog/codec.hpp"

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/avutil.h>
}

#include <x265.h>

namespace cctvlog::codec {

namespace {

std::string ver_string(unsigned v) {
  return std::to_string(AV_VERSION_MAJOR(v)) + "." + std::to_string(AV_VERSION_MINOR(v)) + "." +
         std::to_string(AV_VERSION_MICRO(v));
}

}  // namespace

nlohmann::json ffmpeg_versions() {
  nlohmann::json j;
  j["ffmpeg"] = av_version_info();
  j["libavcodec"] = ver_string(avcodec_version());
  j["libavformat"] = ver_string(avformat_version());
  j["libavutil"] = ver_string(avutil_version());
  j["x265"] = x265_version_str;
  j["x265_build"] = X265_BUILD;
  return j;
}

bool has_encoder(const std::string& name) {
  return avcodec_find_encoder_by_name(name.c_str()) != nullptr;
}

bool has_decoder(const std::string& name) {
  return avcodec_find_decoder_by_name(name.c_str()) != nullptr;
}

}  // namespace cctvlog::codec
