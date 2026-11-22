#pragma once

// DR 확보: 브래킷 HDR 합성, DCG 시뮬레이터 (CLAUDE.md 6.1, 6.2).

#include <cstdint>
#include <span>

#include <nlohmann/json.hpp>

#include "cctvlog/image.hpp"
#include "cctvlog/params.hpp"

namespace cctvlog::hdr {

// 출처 맵 비트 (uint8)
inline constexpr std::uint8_t kSourceIndexMask = 0x0F;  // 입력 순서 프레임 번호 (DCG: 0=HCG, 1=LCG)
inline constexpr std::uint8_t kSourceBlend = 0x40;      // 다음(더 짧은/LCG) 판독과 경계 띠에서 섞임
inline constexpr std::uint8_t kSourceSaturated = 0x80;  // 마지막 판독도 τ 이상

struct BracketFrame {
  const Image<std::uint16_t>* raw = nullptr;  // 1채널 Bayer, right-shift 완료
  double exposure_us = 0.0;                   // 실제 노출 (메타데이터)
  double black = 0.0;
  double white = 0.0;
};

struct MergeResult {
  Image<float> merged;          // 긴 노출 스케일 선형값 (최대 ≈ t_long / t_short)
  Image<std::uint8_t> source;   // 출처 맵
};

// 화소 계산은 double로 하고 결과만 float32로 저장한다 (Python 기준 모델과 상대오차 ≤ 1e-5).
// 프레임은 1~4장, 모두 같은 크기. 노출 내림차순으로 보며 같은 노출은 입력 순서를 따른다.
MergeResult bracket_merge(std::span<const BracketFrame> frames, const BracketMergeParams& p);

// ---- DCG ----------------------------------------------------------------------------------
struct DcgResult {
  Image<float> merged;         // 입력과 같은 선형 스케일
  Image<std::uint8_t> source;  // 0 = HCG, 1 = LCG (+ kSourceBlend, kSourceSaturated)
};

// 화소 하나의 두 판독 (테스트용으로 공개)
struct DcgReadout {
  double hcg_norm;  // HCG 정규화 값 (1.0 = HCG full well)
  double lcg_norm;
  double hcg_x;     // 선형 스케일로 환산
  double lcg_x;
};

// 장면(선형, 음수는 0으로) → 전자 수 λ = x·S → Poisson 1회(두 판독 공유) → 판독별 read noise,
// full well 클립, ADC 양자화(round-half-up, 블랙 오프셋, [0, 2^bits − 1]) → HCG/LCG 합성.
// 난수는 행마다 (seed, 행 번호) 스트림이라 스레드 수와 무관하게 같은 결과.
DcgResult dcg_simulate(const Image<float>& scene, const DcgParams& p);

// 합성 규칙 (HCG 정규화 값 기준, 브래킷과 같은 τ·띠)
double dcg_merge_pixel(const DcgReadout& r, const DcgParams& p, std::uint8_t& source);

// 5.4 스키마 해석적 노이즈 분포 (kind = dcg, τ에서 점프). python merged_noise.dcg_model과 같다.
nlohmann::json dcg_noise_model(const DcgParams& p);

}  // namespace cctvlog::hdr
