"""경매 입찰가 계산 (순수 함수).

P = 거래소 최저가, N = 파티 인원(4 또는 8)

- 판매 손익분기 = floor(P × 0.95 × (N−1)/N)
- 추천 입찰가   = floor(P × 0.95 × (N−1)/N ÷ 1.1)
- 사용 손익분기 = floor(P × (N−1)/N)

부동소수 오차를 피하려고 모든 중간값은 Fraction으로 두고 마지막에 한 번만 내림한다.
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import NamedTuple

PARTY_SIZES = (4, 8)
SELL_FEE_RATE = Fraction(95, 100)  # 거래소 판매 수수료 5% 차감
PREEMPT_DIVISOR = Fraction(11, 10)  # 선점 입찰 기준 1.1배


class CalcResult(NamedTuple):
    sell_breakeven: int  # 판매 손익분기
    recommended_bid: int  # 추천 입찰가(선점)
    use_breakeven: int  # 사용 손익분기


def _validate(price: int, party_size: int) -> None:
    if isinstance(party_size, bool) or party_size not in PARTY_SIZES:
        raise ValueError(f"인원은 4 또는 8이어야 합니다: {party_size!r}")
    if isinstance(price, bool) or not isinstance(price, int):
        raise ValueError(f"가격은 정수여야 합니다: {price!r}")
    if price < 0:
        raise ValueError(f"가격은 0 이상이어야 합니다: {price!r}")


def share_ratio(party_size: int) -> Fraction:
    """본인을 제외한 인원 비율 (N−1)/N."""
    return Fraction(party_size - 1, party_size)


def calculate(price: int, party_size: int) -> CalcResult:
    _validate(price, party_size)
    p = Fraction(price)
    ratio = share_ratio(party_size)
    sell_exact = p * SELL_FEE_RATE * ratio
    return CalcResult(
        sell_breakeven=math.floor(sell_exact),
        recommended_bid=math.floor(sell_exact / PREEMPT_DIVISOR),
        use_breakeven=math.floor(p * ratio),
    )
