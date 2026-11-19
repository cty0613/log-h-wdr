// Phase 0: FFmpeg + x265 빌드·링크 확인 (CLAUDE.md 4절)
#include <gtest/gtest.h>

#include "cctvlog/codec.hpp"
#include "cctvlog/run_info.hpp"

TEST(CodecEnv, Libx265EncoderAvailable) { EXPECT_TRUE(cctvlog::codec::has_encoder("libx265")); }

TEST(CodecEnv, HevcDecoderAvailable) { EXPECT_TRUE(cctvlog::codec::has_decoder("hevc")); }

TEST(CodecEnv, LibraryVersionsReported) {
  const auto v = cctvlog::library_versions();
  for (const char* key : {"ffmpeg", "libavcodec", "libavformat", "x265", "opencv", "eigen"}) {
    ASSERT_TRUE(v.contains(key)) << key;
    EXPECT_FALSE(v[key].get<std::string>().empty()) << key;
  }
}
