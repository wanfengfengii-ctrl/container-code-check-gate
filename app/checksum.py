"""集装箱箱号（ISO 6346 风格）校验位算法与结构校验。

规则（与需求逐字对应，不做任何大小写或空白归一化）：

* 箱号恰为 11 个字符：3 个大写字母（箱主代码）+ 1 个设备类别码
  （仅 U/J/Z）+ 6 位数字（顺序号）+ 1 位校验数字。
* 前 10 位从左到右编号 0..9 参与加权：
  - 数字取其原值 0..9；
  - 字母从 A=10 起递增，凡 11 的倍数一律跳过，因此 B=12、K=21、
    L=23、Z=38（没有任何字母取到 11/22/33）；
  - 第 i 位字符值乘以 2**i，求和后对 11 取余；余数为 10 时期望
    校验位为 0，其余余数即为期望校验位。
"""

from __future__ import annotations

from dataclasses import dataclass

CONTAINER_LENGTH = 11
CATEGORY_IDENTIFIERS = frozenset("UJZ")
MODULUS = 11
REMAINDER_FOR_ZERO_DIGIT = 10  # 余数 10 映射为校验位 0


def _build_letter_values() -> dict[str, int]:
    """构造字母->数值映射：A=10，递增并跳过所有 11 的倍数。"""
    values: dict[str, int] = {}
    value = 10
    for code in range(ord("A"), ord("Z") + 1):
        values[chr(code)] = value
        value += 1
        if value % MODULUS == 0:
            value += 1
    return values


#: 26 个大写字母的字符值，例如 {"A": 10, "B": 12, ..., "Z": 38}
LETTER_VALUES: dict[str, int] = _build_letter_values()


def letter_value(letter: str) -> int:
    """返回单个大写字母的字符值；非大写 A-Z 抛出 KeyError。"""
    return LETTER_VALUES[letter]


def character_value(char: str) -> int:
    """返回单个参与加权字符的值：数字取原值，大写字母按跳号表取值。"""
    if "0" <= char <= "9":
        return ord(char) - ord("0")
    return LETTER_VALUES[char]


@dataclass(frozen=True)
class ChecksumStep:
    """前 10 位中单个字符的加权计算步骤（不可变）。

    ``position`` 从 1 起计，与 :class:`StructureError` 的位置口径一致；
    ``weight`` 为 ``2 ** (position - 1)``；``product`` 为
    ``value * weight``。现场复核时按此逐步骤对账。
    """

    position: int
    character: str
    value: int
    weight: int
    product: int


def checksum_steps(first_ten: str) -> tuple[ChecksumStep, ...]:
    """把参与加权的字符逐位展开为不可变计算步骤（按原位置顺序）。

    这是加权计算的唯一来源：:func:`weighted_sum` 与
    :func:`explain_check_digit` 的汇总值都由这些步骤的 ``product``
    求和得到，避免明细与校验各算一套而产生分歧。
    """
    steps: list[ChecksumStep] = []
    for index, char in enumerate(first_ten):
        value = character_value(char)
        weight = 2 ** index
        steps.append(
            ChecksumStep(
                position=index + 1,
                character=char,
                value=value,
                weight=weight,
                product=value * weight,
            )
        )
    return tuple(steps)


def weighted_sum(first_ten: str) -> int:
    """对前 10 位字符按 2**0 .. 2**9 加权求和（由计算步骤汇总）。"""
    return sum(step.product for step in checksum_steps(first_ten))


def _check_digit_from_remainder(remainder: int) -> int:
    """把取模余数折叠为期望校验位：余数 10 记为 0，其余余数原样。"""
    if remainder == REMAINDER_FOR_ZERO_DIGIT:
        return 0
    return remainder


def expected_check_digit(first_ten: str) -> int:
    """由前 10 位计算期望校验位（余数 10 折叠为 0）。"""
    return _check_digit_from_remainder(weighted_sum(first_ten) % MODULUS)


@dataclass(frozen=True)
class ChecksumExplanation:
    """单箱校验位计算的完整明细：不可变步骤序列与由步骤派生的汇总。

    ``weighted_sum`` 是 ``steps`` 各项 ``product`` 之和，``remainder``
    是合计对 11 的余数，``expected_check_digit`` 是余数折叠结果；与
    :func:`weighted_sum`、:func:`expected_check_digit` 同源同口径。
    """

    container_number: str
    steps: tuple[ChecksumStep, ...]
    weighted_sum: int
    remainder: int
    expected_check_digit: int
    actual_check_digit: int
    passed: bool


def explain_check_digit(container_number: str) -> ChecksumExplanation:
    """生成单箱逐字符计算明细，供校验结论争议时现场复核。

    输入必须恰为 11 位且结构合法（路由层以 :func:`structure_error`
    把关）；不做任何大小写或空白归一化。汇总值全部由本次生成的步骤
    求和、取模、折叠得到，保证明细与结论永远一致。
    """
    if len(container_number) != CONTAINER_LENGTH:
        raise ValueError(
            f"container number must be exactly {CONTAINER_LENGTH} "
            f"characters, got {len(container_number)}"
        )

    steps = checksum_steps(container_number[:10])
    total = sum(step.product for step in steps)
    remainder = total % MODULUS
    expected = _check_digit_from_remainder(remainder)
    actual = int(container_number[10])
    return ChecksumExplanation(
        container_number=container_number,
        steps=steps,
        weighted_sum=total,
        remainder=remainder,
        expected_check_digit=expected,
        actual_check_digit=actual,
        passed=expected == actual,
    )


@dataclass(frozen=True)
class StructureError:
    """结构非法时定位首个损坏位置（position 从 1 起计）。"""

    code: str
    position: int
    message: str


def structure_error(container_number: str) -> StructureError | None:
    """返回箱号的首个结构问题；全部合法时返回 None。

    逐位、从左到右检查，保证报出的是该字符串中最先损坏的位置。
    """
    if len(container_number) != CONTAINER_LENGTH:
        return StructureError(
            code="invalid_length",
            position=min(len(container_number), CONTAINER_LENGTH) + 1,
            message=(
                f"container number must be exactly {CONTAINER_LENGTH} "
                f"characters, got {len(container_number)}"
            ),
        )

    for position in range(0, 3):
        char = container_number[position]
        if not ("A" <= char <= "Z"):
            return StructureError(
                code="not_uppercase_letter",
                position=position + 1,
                message=(
                    f"character at position {position + 1} must be an "
                    "uppercase letter A-Z (no case normalization is applied)"
                ),
            )

    category = container_number[3]
    if category not in CATEGORY_IDENTIFIERS:
        return StructureError(
            code="invalid_category_identifier",
            position=4,
            message=(
                "character at position 4 must be one of U, J, Z "
                f"(equipment category identifier), got {category!r}"
            ),
        )

    for position in range(4, 11):
        char = container_number[position]
        if not ("0" <= char <= "9"):
            return StructureError(
                code="not_digit",
                position=position + 1,
                message=(
                    f"character at position {position + 1} must be a digit "
                    "0-9"
                ),
            )

    return None


@dataclass(frozen=True)
class ContainerParts:
    """合法箱号拆分出的字段。"""

    owner_code: str
    category_identifier: str
    serial_number: str
    check_digit: str


def split_container_number(container_number: str) -> ContainerParts:
    """拆分已通过结构校验的箱号。"""
    return ContainerParts(
        owner_code=container_number[0:3],
        category_identifier=container_number[3],
        serial_number=container_number[4:10],
        check_digit=container_number[10],
    )


@dataclass(frozen=True)
class CorrectionCandidate:
    """单字符纠错候选：差异位置、原字符、新字符与完整候选号。

    ``position`` 从 1 起计，与 :class:`StructureError` 的位置口径一致。
    """

    position: int
    original_character: str
    replacement_character: str
    container_number: str


def _allowed_characters(position: int) -> str:
    """返回 0 起计位置上允许出现的字符全集（升序，保证枚举顺序稳定）。"""
    if position < 3:
        return "".join(sorted(LETTER_VALUES))
    if position == 3:
        return "".join(sorted(CATEGORY_IDENTIFIERS))
    return "0123456789"


def correction_candidates(container_number: str) -> list[CorrectionCandidate]:
    """枚举与原值仅一位不同且结构、校验位均合法的候选箱号。

    输入必须恰为 11 位（路由层按请求校验保证）；不做任何大小写或空白
    归一化。逐位、逐字符枚举汉明距离恰为 1 的串，复用本模块的字符映射、
    :func:`structure_error` 结构判定与 :func:`expected_check_digit` 校验位
    计算筛选；原号自身（差异 0 位）与差异多位的号码一律不进入结果。
    返回按（差异位置, 替换字符）稳定排序的候选列表。
    """
    if len(container_number) != CONTAINER_LENGTH:
        raise ValueError(
            f"container number must be exactly {CONTAINER_LENGTH} "
            f"characters, got {len(container_number)}"
        )

    candidates: list[CorrectionCandidate] = []
    for position in range(CONTAINER_LENGTH):
        original = container_number[position]
        for replacement in _allowed_characters(position):
            if replacement == original:
                continue  # 跳过原字符，保证汉明距离恰为 1
            candidate = (
                container_number[:position]
                + replacement
                + container_number[position + 1 :]
            )
            if structure_error(candidate) is not None:
                continue
            if expected_check_digit(candidate[:10]) != int(candidate[10]):
                continue
            candidates.append(
                CorrectionCandidate(
                    position=position + 1,
                    original_character=original,
                    replacement_character=replacement,
                    container_number=candidate,
                )
            )
    candidates.sort(key=lambda c: (c.position, c.replacement_character))
    return candidates
