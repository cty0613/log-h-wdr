#include <gtest/gtest.h>

#include "cctvlog/image.hpp"

using cctvlog::BayerOrder;
using cctvlog::Image;

TEST(Image, ContiguousLayout) {
  Image<float> img(4, 3, 3);
  EXPECT_EQ(img.stride(), 12u);
  EXPECT_TRUE(img.contiguous());
  img.at(2, 1, 1) = 5.0f;
  EXPECT_EQ(img.data()[1 * 12 + 2 * 3 + 1], 5.0f);
}

TEST(Image, ExplicitStride) {
  Image<std::uint16_t> img(5, 2, 1, 8);
  EXPECT_FALSE(img.contiguous());
  img.at(4, 1) = 7;
  EXPECT_EQ(img.row(1)[4], 7);
  EXPECT_EQ(img.data()[8 + 4], 7);
  EXPECT_THROW(Image<float>(5, 2, 1, 4), std::invalid_argument);
}

TEST(Image, BayerOrderRoundTrip) {
  for (auto o : {BayerOrder::RGGB, BayerOrder::GRBG, BayerOrder::GBRG, BayerOrder::BGGR}) {
    EXPECT_EQ(cctvlog::parse_bayer_order(cctvlog::to_string(o)), o);
  }
  EXPECT_THROW(cctvlog::parse_bayer_order("RGBG"), std::invalid_argument);
}
