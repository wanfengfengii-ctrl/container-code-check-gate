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


def weighted_sum(first_ten: str) -> int:
    """对前 10 位字符按 2**0 .. 2**9 加权求和。"""
    return sum(
        character_value(char) * (2 ** position)
        for position, char in enumerate(first_ten)
    )


def expected_check_digit(first_ten: str) -> int:
    """由前 10 位计算期望校验位（余数 10 折叠为 0）。"""
    remainder = weighted_sum(first_ten) % MODULUS
    if remainder == REMAINDER_FOR_ZERO_DIGIT:
        return 0
    return remainder


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
