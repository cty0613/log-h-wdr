// rng: 결정성, 균등·정규·Poisson 분포의 평균·분산 (몬테카를로, 1e5 샘플).
#include <cmath>
#include <cstdint>
#include <vector>

#include <gtest/gtest.h>

#include "cctvlog/random.hpp"

namespace rng = cctvlog::rng;

namespace {
struct Moments {
  double mean, var;
};
template <typename F>
Moments moments(int n, F&& f) {
  double s = 0, s2 = 0;
  std::vector<double> v(static_cast<std::size_t>(n));
  for (auto& x : v) x = f();
  for (double x : v) s += x;
  const double m = s / n;
  for (double x : v) s2 += (x - m) * (x - m);
  return {m, s2 / (n - 1)};
}
}  // namespace

TEST(Random, Deterministic) {
  auto a = rng::Xoshiro256ss::stream(42, 7);
  auto b = rng::Xoshiro256ss::stream(42, 7);
  auto c = rng::Xoshiro256ss::stream(42, 8);
  bool differs = false;
  for (int i = 0; i < 100; ++i) {
    const auto x = a.next();
    EXPECT_EQ(x, b.next());
    differs = differs || x != c.next();
  }
  EXPECT_TRUE(differs);
}

TEST(Random, UniformAndNormal) {
  auto g = rng::Xoshiro256ss(1);
  const auto u = moments(100000, [&] { return g.uniform(); });
  EXPECT_NEAR(u.mean, 0.5, 0.005);
  EXPECT_NEAR(u.var, 1.0 / 12.0, 0.03 / 12.0);
  double z1 = 0;
  bool second = false;
  const auto z = moments(100000, [&] {
    double a, b;
    if (second) {
      second = false;
      return z1;
    }
    rng::normal_pair(g, a, b);
    z1 = b;
    second = true;
    return a;
  });
  EXPECT_NEAR(z.mean, 0.0, 0.015);
  EXPECT_NEAR(z.var, 1.0, 0.03);
}

class PoissonMoments : public ::testing::TestWithParam<double> {};

TEST_P(PoissonMoments, MeanAndVarianceEqualLambda) {
  const double lam = GetParam();
  auto g = rng::Xoshiro256ss::stream(20261122, static_cast<std::uint64_t>(lam * 1000));
  const auto m = moments(100000, [&] { return static_cast<double>(rng::poisson(g, lam)); });
  // 평균: 표준오차 √(λ/n)의 5배, 분산: ±3% (CLAUDE.md 1.1)
  EXPECT_NEAR(m.mean, lam, 5.0 * std::sqrt(lam / 1e5)) << "lambda " << lam;
  EXPECT_NEAR(m.var / lam, 1.0, 0.03) << "lambda " << lam;
}

INSTANTIATE_TEST_SUITE_P(Lambdas, PoissonMoments,
                         ::testing::Values(0.3, 3.0, 9.99, 10.0, 50.0, 1104.0, 17666.0));

TEST(Random, PoissonZero) {
  auto g = rng::Xoshiro256ss(3);
  EXPECT_EQ(rng::poisson(g, 0.0), 0);
  EXPECT_EQ(rng::poisson(g, -1.0), 0);
}
