"""P2 HTML fixture 最小化与脱敏处理。

禁止直接保存 page.content() 原样落盘，必须先进行最小化和脱敏。

脱敏规则：
- 删除：script/style/noscript/iframe/svg/canvas/meta敏感值/preload/prefetch/inline事件/on*属性
- 删除：Cookie/localStorage/sessionStorage片段/Authorization/CSP nonce/token/securityId/traceId/sessionId/UUID追踪值
- 属性白名单：class/id/href/title/aria-label/role/data-*（逐项审核）
- URL 属性脱敏：去 query/fragment，只保留 BOSS 官方域名
- 文本脱敏：手机号/邮箱/身份证号/长token 替换为占位符

保存后执行二次扫描；如果仍发现高风险字段，拒绝写入并报错。
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

# ==================== 允许保留的标签白名单 ====================
# 只保留解析所需的公开结构标签
ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        # 容器与布局
        "div",
        "section",
        "article",
        "main",
        "header",
        "footer",
        "aside",
        "nav",
        "ul",
        "ol",
        "li",
        "dl",
        "dt",
        "dd",
        # 标题与文本
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "span",
        "em",
        "strong",
        "b",
        "i",
        "small",
        "sub",
        "sup",
        "mark",
        "time",
        "abbr",
        # 链接与媒体（图片脱敏后保留 alt/title，src 删除）
        "a",
        "img",
        # 表格（部分岗位信息以表格呈现）
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        # 其他
        "br",
        "hr",
        "label",
    }
)

# ==================== 必须删除的标签 ====================
TAGS_TO_REMOVE: frozenset[str] = frozenset(
    {
        "script",
        "style",
        "noscript",
        "iframe",
        "svg",
        "canvas",
        "link",
        "meta",
        "base",
        "embed",
        "object",
        "template",
        "form",
        "input",
        "button",
        "textarea",
        "select",
        "option",
    }
)

# ==================== 属性白名单 ====================
# 注意：data-* 需逐项审核，不全部无条件保留（见 _sanitize_data_attr）
ALLOWED_ATTRS: frozenset[str] = frozenset(
    {
        "class",
        "id",
        "href",
        "title",
        "aria-label",
        "role",
        "alt",
    }
)

# ==================== 高风险 data-* 关键词 ====================
# 包含这些关键词的 data-* 属性将被删除
HIGH_RISK_DATA_KEYWORDS: tuple[str, ...] = (
    "token",
    "session",
    "security",
    "trace",
    "uid",
    "userid",
    "user-id",
    "encrypt",
    "csrf",
    "nonce",
    "auth",
    "secret",
    "key",
    "uuid",
    "device",
)

# ==================== BOSS 官方域名 ====================
BOSS_HOSTS: frozenset[str] = frozenset({"www.zhipin.com", "zhipin.com"})

# ==================== 文本脱敏正则 ====================
# 中国大陆手机号：1[3-9]开头的11位数字
_PHONE_RE = re.compile(r"1[3-9]\d{9}")

# 邮箱
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# 身份证号（18位，最后一位可能为X）
# 使用 (?<!\d) 和 (?!\d) 替代 \b，避免中文与数字边界在 Unicode 模式下不匹配
_ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")

# 长 token（32位以上的十六进制或base64字符串）
_LONG_TOKEN_RE = re.compile(r"\b[a-fA-F0-9]{32,}\b|[A-Za-z0-9+/=]{40,}\b")

# ==================== P2.1 URL userinfo 形态模式 ====================
# 检测 https://name:password@ 或 https://name@ 形态
# 独立成组便于在 sanitize_html 中于文本脱敏前执行预扫描：
# 文本脱敏（邮箱正则）会消耗 userinfo 的 @，导致后续扫描无法检测，
# 因此必须在 sanitize_text 之前对 userinfo 进行专项扫描。
USERINFO_URL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)https?://[^/\s:@]+:[^/\s@]*@"),
    re.compile(r"(?i)https?://[^/\s:@]+@"),
)

# ==================== 高风险内容关键词（二次扫描） ====================
HIGH_RISK_CONTENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    _PHONE_RE,
    _EMAIL_RE,
    _ID_CARD_RE,
    re.compile(r"(?i)securityid\s*[=:]\s*\S+"),
    re.compile(r"(?i)sessionid\s*[=:]\s*\S+"),
    re.compile(r"(?i)tracid\s*[=:]\s*\S+"),
    re.compile(r"(?i)authorization\s*[=:]\s*\S+"),
    re.compile(r"(?i)cookie\s*[=:]\s*\S+"),
    re.compile(r"(?i)localstorage"),
    re.compile(r"(?i)sessionstorage"),
    re.compile(r"(?i)nonce\s*[=:]\s*['\"][^'\"]+['\"]"),
    # P2.1 新增：URL userinfo 形态防御性扫描
    # 复用 USERINFO_URL_PATTERNS，作为 sanitize_url 遗漏时的兜底
    *USERINFO_URL_PATTERNS,
)


def scan_userinfo(html: str) -> list[str]:
    """P2.1：仅扫描 URL userinfo 形态。

    在 sanitize_html 中于文本脱敏前执行，避免邮箱脱敏（_EMAIL_RE）
    消耗 userinfo 的 @ 导致漏检。userinfo URL 不得出现在 fixture 的
    任何位置（文本节点或属性），发现即拒绝。

    Args:
        html: 待扫描的 HTML 字符串

    Returns:
        违规描述列表（空列表表示通过）
    """
    violations: list[str] = []
    for pattern in USERINFO_URL_PATTERNS:
        if pattern.search(html):
            violations.append(f"命中 URL userinfo 模式 {pattern.pattern[:60]}: 已截断")
    return violations


def sanitize_url(url: str | None, base: str | None = None) -> str | None:
    """标准化并脱敏 URL（P2.1 严格安全版本）。

    严格执行：
    1. URL 非空，相对 URL 通过 urljoin 转绝对 URL。
    2. scheme 必须为 https（HTTP 直接拒绝，不自动升级）。
    3. hostname 必须严格属于 BOSS 官方域名（www.zhipin.com / zhipin.com）。
    4. username 必须为空。
    5. password 必须为空。
    6. 禁止任何显式端口（包括 :443）。
    7. 访问 parsed.port 时处理可能出现的 ValueError。
    8. 删除 query。
    9. 删除 fragment。
    10. 不使用原始 parsed.netloc 构造输出，只使用经过验证的 hostname。

    Args:
        url: 原始 URL
        base: 基础 URL（用于相对 URL 转换）

    Returns:
        脱敏后的 URL（https://host/path），非官方域名/HTTP/userinfo/显式端口均返回 None
    """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None

    # 转绝对 URL
    if base:
        try:
            url = urljoin(base, url)
        except (ValueError, TypeError):
            return None

    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return None

    # 1. scheme 必须为 https（HTTP 不自动升级，直接拒绝）
    if parsed.scheme != "https":
        return None

    # 2. userinfo 严格拒绝（username/password 任一存在即拒绝）
    if parsed.username is not None or parsed.password is not None:
        return None

    # 3. 禁止任何显式端口（包括 :443）；非法端口格式抛 ValueError 时安全返回 None
    try:
        if parsed.port is not None:
            return None
    except ValueError:
        return None

    # 4. hostname 必须严格属于 BOSS 官方域名
    host = (parsed.hostname or "").lower()
    if host not in BOSS_HOSTS:
        return None

    # 5. 路径为空时规范化为 "/"
    path = parsed.path or "/"

    # 6. 只使用经过验证的 hostname 构造输出，不使用原始 netloc
    return f"https://{host}{path}"


def sanitize_text(text: str | None) -> str | None:
    """脱敏文本内容。

    替换：
    - 中国大陆手机号 → <REDACTED_PHONE>
    - 邮箱 → <REDACTED_EMAIL>
    - 身份证号 → <REDACTED_ID>
    - 长 token（32位以上十六进制）→ <REDACTED_TOKEN>

    不会误删岗位描述中的正常数字（只匹配特定格式）。
    """
    if not text:
        return text
    result = text
    result = _ID_CARD_RE.sub("<REDACTED_ID>", result)
    result = _PHONE_RE.sub("<REDACTED_PHONE>", result)
    result = _EMAIL_RE.sub("<REDACTED_EMAIL>", result)
    result = _LONG_TOKEN_RE.sub("<REDACTED_TOKEN>", result)
    return result


def _normalize_whitespace(text: str) -> str:
    """规范化空白：去除首尾空白，合并连续空白为单个空格，保留换行。"""
    if not text:
        return text
    # 保留换行，合并其他连续空白
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        # 合并行内连续空白
        cleaned = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        cleaned_lines.append(cleaned)
    return "\n".join(cleaned_lines).strip()


def _is_high_risk_data_attr(attr_name: str) -> bool:
    """判断 data-* 属性是否高风险（应删除）。"""
    name_lower = attr_name.lower()
    return any(kw in name_lower for kw in HIGH_RISK_DATA_KEYWORDS)


def _sanitize_tag(tag: Tag, base_url: str | None) -> None:
    """递归脱敏单个标签及其子孙。"""
    # 删除不在白名单的属性
    attrs_to_remove = []
    for attr_name in list(tag.attrs.keys()):
        attr_lower = attr_name.lower()
        # 删除所有 on* 事件属性
        if attr_lower.startswith("on"):
            attrs_to_remove.append(attr_name)
            continue
        # data-* 逐项审核
        if attr_lower.startswith("data-"):
            if _is_high_risk_data_attr(attr_name):
                attrs_to_remove.append(attr_name)
            continue
        # 非白名单属性删除
        if attr_lower not in ALLOWED_ATTRS:
            attrs_to_remove.append(attr_name)

    for attr in attrs_to_remove:
        del tag.attrs[attr]

    # href 脱敏
    if tag.has_attr("href"):
        href = tag["href"]
        if isinstance(href, list):
            href = href[0] if href else ""
        sanitized = sanitize_url(href, base=base_url)
        if sanitized:
            tag["href"] = sanitized
        else:
            # 非官方域名 href 删除（避免泄露外部链接）
            del tag["href"]

    # img src 删除（可能含追踪参数），保留 alt/title
    if tag.name == "img" and tag.has_attr("src"):
        del tag.attrs["src"]


def sanitize_html(html: str, base_url: str | None = None) -> str:
    """HTML 最小化与脱敏处理。

    步骤：
    1. BeautifulSoup 解析
    2. 删除所有 TAGS_TO_REMOVE 标签（含内容）
    3. unwrap 不在白名单的标签（保留文本与子节点）
    4. 删除非白名单属性与 on* 事件
    5. URL 属性脱敏（href 含 userinfo/端口时删除该属性）
    6. P2.1 userinfo 预扫描：在文本脱敏前检查 userinfo 形态
       （文本脱敏的邮箱正则会消耗 userinfo 的 @，导致后续扫描漏检）
    7. 文本节点脱敏（手机号/邮箱/身份证/token）
    8. 空白规范化
    9. 二次扫描高风险内容，发现则抛 ValueError

    Args:
        html: 原始 HTML 字符串
        base_url: 基础 URL（用于相对 URL 转换与域名校验）

    Returns:
        脱敏后的 HTML 字符串

    Raises:
        ValueError: 二次扫描发现高风险内容
    """
    if not html or not html.strip():
        return ""

    soup = BeautifulSoup(html, "lxml")

    # 1. 删除 TAGS_TO_REMOVE 标签（含内容）
    for tag_name in TAGS_TO_REMOVE:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # 2. unwrap 不在白名单的标签（保留文本与子节点）
    # 注意：需要从内到外处理，避免破坏遍历
    # 多次扫描确保嵌套的非白名单标签都被 unwrap
    for _ in range(3):
        for tag in soup.find_all(True):
            if tag.name not in ALLOWED_TAGS:
                tag.unwrap()

    # 3. 递归处理所有标签的属性与文本
    for tag in soup.find_all(True):
        _sanitize_tag(tag, base_url)

    # 4. P2.1 userinfo 预扫描：在文本脱敏前检查 userinfo 形态
    #    此时 href 中的 userinfo 已被 _sanitize_tag 删除，
    #    若中间结果仍含 userinfo，必然出现在文本节点或非白名单属性中，
    #    无法通过后续脱敏安全清理，必须直接拒绝。
    #    必须在 sanitize_text 之前执行：邮箱正则 _EMAIL_RE 会匹配
    #    user@host 形态并将其替换为 <REDACTED_EMAIL>，从而消耗 userinfo
    #    的 @，使最终二次扫描无法检出。
    intermediate = str(soup)
    userinfo_violations = scan_userinfo(intermediate)
    if userinfo_violations:
        raise ValueError(
            "二次扫描发现高风险内容，拒绝保存 fixture：\n  - " + "\n  - ".join(userinfo_violations)
        )

    # 5. 脱敏所有文本节点
    for text_node in soup.find_all(string=True):
        if isinstance(text_node, NavigableString):
            sanitized = sanitize_text(str(text_node))
            normalized = _normalize_whitespace(sanitized) if sanitized else sanitized
            if normalized != str(text_node):
                text_node.replace_with(NavigableString(normalized))

    # 6. 输出
    result = str(soup)

    # 7. 二次扫描高风险内容
    violations = scan_high_risk_content(result)
    if violations:
        raise ValueError(
            "二次扫描发现高风险内容，拒绝保存 fixture：\n  - " + "\n  - ".join(violations)
        )

    return result


def scan_high_risk_content(html: str) -> list[str]:
    """扫描 HTML 中的高风险内容（二次校验）。

    Returns:
        违规描述列表（空列表表示通过）
    """
    violations: list[str] = []
    for pattern in HIGH_RISK_CONTENT_PATTERNS:
        matches = pattern.findall(html)
        if matches:
            # 不报告具体示例，避免泄露敏感内容
            violations.append(f"命中模式 {pattern.pattern[:60]}: {len(matches)} 处，示例已截断")
    return violations


def compute_sha256(content: str) -> str:
    """计算内容的 SHA256 哈希（用于 fixture 元数据完整性校验）。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


__all__ = [
    "ALLOWED_TAGS",
    "TAGS_TO_REMOVE",
    "ALLOWED_ATTRS",
    "BOSS_HOSTS",
    "USERINFO_URL_PATTERNS",
    "sanitize_url",
    "sanitize_text",
    "sanitize_html",
    "scan_userinfo",
    "scan_high_risk_content",
    "compute_sha256",
]
