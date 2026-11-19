#pragma once

#include <string>

#include <nlohmann/json.hpp>

// 인코딩·디코딩, RGB↔YCbCr 변환 (Phase 5). 현재는 빌드 환경 확인용 질의만 제공한다.
namespace cctvlog::codec {

// libavcodec / libavformat / libavutil / x265 버전
nlohmann::json ffmpeg_versions();

bool has_encoder(const std::string& name);  // 예: "libx265"
bool has_decoder(const std::string& name);  // 예: "hevc"

}  // namespace cctvlog::codec
