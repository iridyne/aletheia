"""Property-based tests using Hypothesis.

覆盖有明确不变量的核心纯函数：
- knowledge._is_quality_pass  质量门
- data.symbols  符号格式转换往返一致性
- data.symbols  转换安全性（任意输入不崩溃）
"""

import pytest
from hypothesis import given, assume, strategies as st

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# 字符串策略：可打印 ASCII，最多 300 字符
_text = st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=300)
_nonempty_text = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=1, max_size=300,
)

# 合法交易对 base/quote（模拟真实用例）
_BASES = ["BTC", "ETH", "SOL", "AAPL", "TSLA", "NVDA"]
_QUOTES = ["USDT", "USDC", "USD", "BTC", "ETH"]


# ---------------------------------------------------------------------------
# Quality gate (_is_quality_pass)
# ---------------------------------------------------------------------------

class TestQualityGate:
    """_is_quality_pass 的不变量：
    - 长度 < 80 的内容必须被拒绝
    - 含 placeholder 子串的内容必须被拒绝
    - 长度 ≥ 80 且不含 placeholder 的内容必须通过
    """

    def setup_method(self):
        from aletheia.knowledge.manager import _is_quality_pass, _MIN_CONTENT_LENGTH, _PLACEHOLDER_PATTERNS
        self._gate = _is_quality_pass
        self._min_len = _MIN_CONTENT_LENGTH
        self._placeholders = _PLACEHOLDER_PATTERNS

    @given(st.text(max_size=79))
    def test_short_content_always_rejected(self, content):
        """任何 strip 后长度 < 80 的内容都必须被拒绝。"""
        assume(len(content.strip()) < self._min_len)
        assert self._gate(content) is False

    @given(st.sampled_from([
        "Content here",
        "(LLM unavailable",
        "Synthesized 3 research notes",
        "LLM unavailable for synthesis",
    ]), st.text(min_size=40, max_size=100), st.text(min_size=40, max_size=100))
    def test_placeholder_always_rejected(self, placeholder, prefix, suffix):
        """含 placeholder 子串的内容，无论前后补多少字符，都必须被拒绝。"""
        content = prefix + placeholder + suffix
        assert self._gate(content) is False

    @given(st.text(
        alphabet=st.characters(min_codepoint=65, max_codepoint=122),  # A-z
        min_size=80, max_size=200,
    ))
    def test_clean_long_content_always_passes(self, content):
        """长度 ≥ 80 且不含任何 placeholder 子串的纯字母内容必须通过。"""
        assume(not any(p in content for p in self._placeholders))
        assert self._gate(content) is True

    @given(st.integers(min_value=0, max_value=79))
    def test_boundary_exactly_at_limit(self, length):
        """strip 后长度 < 80 必须被拒绝（边界值）。"""
        content = "A" * length
        assert self._gate(content) is False

    def test_boundary_exactly_80_passes(self):
        """恰好 80 个字符的干净内容必须通过。"""
        assert self._gate("A" * 80) is True


# ---------------------------------------------------------------------------
# Symbol conversion (to_canonical / to_bitget / to_ccxt_perp / to_yfinance)
# ---------------------------------------------------------------------------

class TestSymbolConversions:
    """符号转换的核心不变量：
    - to_canonical 幂等：对已规范化的符号再次调用结果不变
    - to_bitget / to_ccxt_perp / to_yfinance 对任意字符串不崩溃
    - 标准格式 BASE/QUOTE → to_bitget 消除斜杠
    - to_canonical → to_bitget → to_canonical 往返一致
    """

    def setup_method(self):
        from aletheia.data.symbols import to_canonical, to_bitget, to_ccxt_perp, to_yfinance
        self.to_canonical = to_canonical
        self.to_bitget = to_bitget
        self.to_ccxt_perp = to_ccxt_perp
        self.to_yfinance = to_yfinance

    @given(_text)
    def test_to_canonical_never_crashes(self, symbol):
        """to_canonical 对任意字符串都不应抛出异常。"""
        result = self.to_canonical(symbol)
        assert isinstance(result, str)

    @given(_text)
    def test_to_bitget_never_crashes(self, symbol):
        """to_bitget 对任意字符串都不应抛出异常。"""
        result = self.to_bitget(symbol)
        assert isinstance(result, str)

    @given(_text)
    def test_to_ccxt_perp_never_crashes(self, symbol):
        """to_ccxt_perp 对任意字符串都不应抛出异常。"""
        result = self.to_ccxt_perp(symbol)
        assert isinstance(result, str)

    @given(_text)
    def test_to_yfinance_never_crashes(self, symbol):
        """to_yfinance 对任意字符串都不应抛出异常。"""
        result = self.to_yfinance(symbol)
        assert isinstance(result, str)

    @given(st.sampled_from(_BASES), st.sampled_from(_QUOTES))
    def test_canonical_is_idempotent(self, base, quote):
        """to_canonical 对已规范化格式幂等：二次调用结果不变。"""
        canonical = f"{base}/{quote}"
        assert self.to_canonical(canonical) == self.to_canonical(self.to_canonical(canonical))

    @given(st.sampled_from(_BASES), st.sampled_from(_QUOTES))
    def test_canonical_format_has_single_slash(self, base, quote):
        """to_canonical 结果格式：有且只有一个斜杠（或为空）。"""
        result = self.to_canonical(f"{base}/{quote}")
        if result:
            assert result.count("/") == 1

    @given(st.sampled_from(_BASES), st.sampled_from(_QUOTES))
    def test_to_bitget_removes_slash(self, base, quote):
        """to_bitget 结果不含斜杠。"""
        result = self.to_bitget(f"{base}/{quote}")
        assert "/" not in result

    @given(st.sampled_from(_BASES), st.sampled_from(_QUOTES))
    def test_ccxt_perp_contains_colon(self, base, quote):
        """to_ccxt_perp 对 BASE/QUOTE 格式结果包含冒号（settle suffix）。"""
        canon = f"{base}/{quote}"
        result = self.to_ccxt_perp(canon)
        assert ":" in result

    @given(st.sampled_from(["BTC/USDT", "ETH/USDT", "SOL/USDT"]))
    def test_roundtrip_canonical_bitget_canonical(self, symbol):
        """canonical → bitget → canonical 往返一致（仅限已知标准对）。"""
        bitget_form = self.to_bitget(symbol)
        back = self.to_canonical(bitget_form)
        assert back == symbol

    @given(st.sampled_from(["BTC/USDT", "btc/usdt", "BTCUSDT", "BTC-USDT"]))
    def test_common_formats_all_normalize_to_same(self, variant):
        """BTC/USDT 的各种变体都应规范化为同一个结果。"""
        assert self.to_canonical(variant) == "BTC/USDT"


# ---------------------------------------------------------------------------
# content_hash 稳定性
# ---------------------------------------------------------------------------

class TestContentHash:
    """_content_hash 的不变量：
    - 相同内容总是返回相同 hash
    - 前后空白不影响 hash
    - 不同内容（大概率）返回不同 hash
    """

    def setup_method(self):
        from aletheia.knowledge.manager import _content_hash
        self._hash = _content_hash

    @given(_nonempty_text)
    def test_deterministic(self, content):
        """相同内容两次调用结果必须相同。"""
        assert self._hash(content) == self._hash(content)

    @given(_nonempty_text)
    def test_whitespace_stripped(self, content):
        """前后空白不影响 hash 值。"""
        assert self._hash(content) == self._hash(f"  {content}  ")

    @given(_nonempty_text)
    def test_returns_16_hex_chars(self, content):
        """返回值恰好是 16 位十六进制字符串。"""
        h = self._hash(content)
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    @given(_nonempty_text, _nonempty_text)
    def test_different_content_usually_different_hash(self, a, b):
        """不同内容大概率返回不同 hash（允许极低概率碰撞）。"""
        assume(a.strip() != b.strip())
        # SHA-256 碰撞概率 1/2^64，实际不可能触发
        assert self._hash(a) != self._hash(b)
