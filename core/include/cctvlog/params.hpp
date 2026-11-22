#pragma once

// 블록별 파라미터 + JSON 직렬화 (CLAUDE.md 3절, 8절: Params는 JSON 직렬화 + 기본값).
// from_json은 없는 키에 기본값을 쓰고, 모르는 키는 오타로 보고 예외를 던진다.

#include <cstdint>
#include <initializer_list>
#include <optional>
#include <stdexcept>
#include <string>

#include <nlohmann/json.hpp>

namespace cctvlog {

namespace detail {

inline void reject_unknown_keys(const nlohmann::json& j, std::initializer_list<const char*> keys,
                                const char* what) {
  if (!j.is_object()) throw std::invalid_argument(std::string(what) + ": expected JSON object");
  for (const auto& [k, v] : j.items()) {
    bool known = false;
    for (const char* key : keys) known = known || k == key;
    // "_"로 시작하는 키는 주석용으로 허용
    if (!known && k.rfind('_', 0) != 0) {
      throw std::invalid_argument(std::string(what) + ": unknown key '" + k + "'");
    }
  }
}

template <typename T>
void get_opt(const nlohmann::json& j, const char* key, T& out) {
  if (auto it = j.find(key); it != j.end() && !it->is_null()) out = it->get<T>();
}

}  // namespace detail

// ---- 브래킷 HDR 합성 (CLAUDE.md 6.1) ----------------------------------------------------
struct BracketMergeParams {
  double tau = 0.9;         // 포화 임계 (정규화 값)
  double band_ratio = 0.8;  // 경계 띠 [band_ratio·τ, τ]

  void validate() const {
    if (!(tau > 0.0 && tau <= 1.0)) throw std::invalid_argument("bracket_merge: tau in (0, 1]");
    if (!(band_ratio > 0.0 && band_ratio < 1.0)) {
      throw std::invalid_argument("bracket_merge: band_ratio in (0, 1)");
    }
  }
};

inline void to_json(nlohmann::json& j, const BracketMergeParams& p) {
  j = {{"tau", p.tau}, {"band_ratio", p.band_ratio}};
}

inline void from_json(const nlohmann::json& j, BracketMergeParams& p) {
  detail::reject_unknown_keys(j, {"tau", "band_ratio"}, "bracket_merge params");
  p = BracketMergeParams{};
  detail::get_opt(j, "tau", p.tau);
  detail::get_opt(j, "band_ratio", p.band_ratio);
  p.validate();
}

// ---- DCG 시뮬레이터 (CLAUDE.md 6.2, docs/decisions.md 2026-11-22 결정 3) ---------------------
// 기본값 = configs/dcg_assumptions.json (변환 게인 비 4, 옵션 B).
struct DcgParams {
  double conversion_gain_ratio = 4.0;
  double lcg_full_well_e = 17666.0;  // Phase 1 g1 포화 전자 수
  double lcg_read_noise_e = 9.96;    // g1 판독 노이즈에서 ADC 양자화를 뺀 값
  // 없으면 LCG ÷ conversion_gain_ratio (이상적 규칙)
  std::optional<double> hcg_full_well_e;
  std::optional<double> hcg_read_noise_e;
  double scene_scale_e_per_unit = 17666.0 / 16.0;  // x = 16에서 LCG 포화
  int adc_bits = 10;
  double black_level_dn = 64.0;
  double tau = 0.9;
  double band_ratio = 0.8;
  std::uint64_t seed = 20261122;

  double hcg_fw() const { return hcg_full_well_e.value_or(lcg_full_well_e / conversion_gain_ratio); }
  double hcg_rn() const {
    return hcg_read_noise_e.value_or(lcg_read_noise_e / conversion_gain_ratio);
  }
  double adc_full_scale_dn() const {
    return static_cast<double>((std::uint64_t{1} << adc_bits) - 1) - black_level_dn;
  }

  void validate() const {
    auto req = [](bool ok, const char* msg) {
      if (!ok) throw std::invalid_argument(std::string("dcg params: ") + msg);
    };
    req(conversion_gain_ratio >= 1.0, "conversion_gain_ratio >= 1");
    req(lcg_full_well_e > 0.0 && hcg_fw() > 0.0, "full wells > 0");
    req(hcg_fw() <= lcg_full_well_e, "hcg_full_well_e <= lcg_full_well_e");
    req(lcg_read_noise_e >= 0.0 && hcg_rn() >= 0.0, "read noise >= 0");
    req(scene_scale_e_per_unit > 0.0, "scene_scale_e_per_unit > 0");
    req(adc_bits >= 8 && adc_bits <= 16, "adc_bits in [8, 16]");
    req(black_level_dn >= 0.0 && adc_full_scale_dn() > 0.0, "black_level_dn in range");
    req(tau > 0.0 && tau <= 1.0, "tau in (0, 1]");
    req(band_ratio > 0.0 && band_ratio < 1.0, "band_ratio in (0, 1)");
  }
};

inline void to_json(nlohmann::json& j, const DcgParams& p) {
  // 파생값까지 모두 명시 (run.json, 노이즈 JSON assumptions)
  j = {{"conversion_gain_ratio", p.conversion_gain_ratio},
       {"lcg_full_well_e", p.lcg_full_well_e},
       {"lcg_read_noise_e", p.lcg_read_noise_e},
       {"hcg_full_well_e", p.hcg_fw()},
       {"hcg_read_noise_e", p.hcg_rn()},
       {"scene_scale_e_per_unit", p.scene_scale_e_per_unit},
       {"adc_bits", p.adc_bits},
       {"black_level_dn", p.black_level_dn},
       {"tau", p.tau},
       {"band_ratio", p.band_ratio},
       {"seed", p.seed}};
}

inline void from_json(const nlohmann::json& j, DcgParams& p) {
  detail::reject_unknown_keys(
      j,
      {"conversion_gain_ratio", "lcg_full_well_e", "lcg_read_noise_e", "hcg_full_well_e",
       "hcg_read_noise_e", "scene_scale_e_per_unit", "adc_bits", "black_level_dn", "tau",
       "band_ratio", "seed"},
      "dcg params");
  p = DcgParams{};
  detail::get_opt(j, "conversion_gain_ratio", p.conversion_gain_ratio);
  detail::get_opt(j, "lcg_full_well_e", p.lcg_full_well_e);
  detail::get_opt(j, "lcg_read_noise_e", p.lcg_read_noise_e);
  if (auto it = j.find("hcg_full_well_e"); it != j.end() && !it->is_null()) {
    p.hcg_full_well_e = it->get<double>();
  }
  if (auto it = j.find("hcg_read_noise_e"); it != j.end() && !it->is_null()) {
    p.hcg_read_noise_e = it->get<double>();
  }
  detail::get_opt(j, "scene_scale_e_per_unit", p.scene_scale_e_per_unit);
  detail::get_opt(j, "adc_bits", p.adc_bits);
  detail::get_opt(j, "black_level_dn", p.black_level_dn);
  detail::get_opt(j, "tau", p.tau);
  detail::get_opt(j, "band_ratio", p.band_ratio);
  detail::get_opt(j, "seed", p.seed);
  p.validate();
}

}  // namespace cctvlog
