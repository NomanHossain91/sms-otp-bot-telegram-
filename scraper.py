import requests
from bs4 import BeautifulSoup
import re
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class IVASMSScraper:
    """Improved IVASMS.com scraper with robust login & OTP extraction"""

    BASE_URL = "https://www.ivasms.com"

    HEADERS = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        ),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }

    SMS_PATHS = ['/portal/sms', '/sms', '/messages', '/inbox', '/dashboard', '/portal']

    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self.is_logged_in = False
        self._login_attempts = 0

    # ── Login ──────────────────────────────────────────────────────

    def login(self) -> bool:
        self._login_attempts += 1
        logger.info(f"Login attempt #{self._login_attempts} for {self.email}")

        try:
            # Step 1: GET login page → extract CSRF token
            resp = self.session.get(f"{self.BASE_URL}/login", timeout=15)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.content, 'html.parser')
            csrf = self._get_csrf(soup)

            # Step 2: POST credentials
            payload = {'email': self.email, 'password': self.password}
            if csrf:
                payload['_token'] = csrf

            login_resp = self.session.post(
                f"{self.BASE_URL}/login",
                data=payload,
                timeout=15,
                allow_redirects=True
            )

            # Step 3: Verify success
            if self._verify_login(login_resp):
                self.is_logged_in = True
                logger.info("✅ Login successful")
                return True

            logger.error("❌ Login failed — check credentials")
            return False

        except requests.RequestException as e:
            logger.error(f"Login request error: {e}")
            return False

    def _get_csrf(self, soup: BeautifulSoup) -> str | None:
        """Extract CSRF token from page"""
        for name in ['_token', 'csrf_token', 'csrfmiddlewaretoken']:
            tag = soup.find('input', {'name': name})
            if tag:
                return tag.get('value')
        meta = soup.find('meta', {'name': 'csrf-token'})
        if meta:
            return meta.get('content')
        return None

    def _verify_login(self, resp: requests.Response) -> bool:
        """Check if login response indicates success"""
        # Redirect to dashboard/portal
        if any(kw in resp.url for kw in ['dashboard', 'portal', 'account', 'home']):
            return True
        soup = BeautifulSoup(resp.content, 'html.parser')
        # Logout link present = logged in
        if soup.find('a', href=re.compile(r'logout', re.I)):
            return True
        # No login form = logged in
        if not soup.find('input', {'name': 'password'}):
            return True
        return False

    # ── Fetch Messages ─────────────────────────────────────────────

    def fetch_messages(self) -> list[dict]:
        """Fetch OTPs from IVASMS, auto-login if needed"""
        if not self.is_logged_in:
            if not self.login():
                return []

        messages = []

        for path in self.SMS_PATHS:
            try:
                url = f"{self.BASE_URL}{path}"
                resp = self.session.get(url, timeout=15)

                # Session expired
                if 'login' in resp.url or resp.status_code == 401:
                    logger.warning("Session expired, re-logging in...")
                    self.is_logged_in = False
                    if not self.login():
                        return []
                    resp = self.session.get(url, timeout=15)

                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.content, 'html.parser')
                    found = self._extract_messages(soup)
                    if found:
                        logger.info(f"Found {len(found)} messages at {path}")
                        messages.extend(found)
                        break  # Stop once we find messages

            except requests.RequestException as e:
                logger.debug(f"Path {path} failed: {e}")
                continue

        return messages

    # ── Extraction ─────────────────────────────────────────────────

    def _extract_messages(self, soup: BeautifulSoup) -> list[dict]:
        """Try all extraction methods"""
        results = []

        # Method 1: Tables (most common for IVASMS)
        results.extend(self._from_tables(soup))
        if results:
            return results

        # Method 2: Structured divs
        results.extend(self._from_divs(soup))
        if results:
            return results

        # Method 3: Raw text scan (last resort)
        results.extend(self._from_text(soup))
        return results

    def _from_tables(self, soup: BeautifulSoup) -> list[dict]:
        messages = []
        for table in soup.find_all('table'):
            rows = table.find_all('tr')
            for row in rows[1:]:  # skip header
                cells = [c.get_text(strip=True) for c in row.find_all(['td', 'th'])]
                if len(cells) < 2:
                    continue
                msg = self._parse_cells(cells)
                if msg:
                    messages.append(msg)
        return messages

    def _from_divs(self, soup: BeautifulSoup) -> list[dict]:
        messages = []
        selectors = ['div.sms-item', 'div.message-item', 'div.otp-item',
                     'li.sms', 'tr.sms', '.inbox-item']
        for sel in selectors:
            for el in soup.select(sel):
                text = el.get_text(separator=' ', strip=True)
                msg = self._parse_raw_text(text)
                if msg:
                    messages.append(msg)
        return messages

    def _from_text(self, soup: BeautifulSoup) -> list[dict]:
        """Scan full page text for OTP patterns"""
        messages = []
        text = soup.get_text(separator=' ')
        # Find all standalone 4-8 digit numbers (likely OTPs)
        otp_pattern = re.compile(r'\b(\d{4,8})\b')
        for m in otp_pattern.finditer(text):
            otp = m.group(1)
            context_start = max(0, m.start() - 150)
            context_end = min(len(text), m.end() + 150)
            context = text[context_start:context_end]

            # Only include if context mentions OTP/verification/code
            if re.search(r'otp|verification|code|pin|confirm', context, re.I):
                messages.append({
                    'otp': otp,
                    'phone': self._find_phone(context),
                    'service': self._find_service(context),
                    'timestamp': datetime.now().strftime('%H:%M:%S'),
                    'raw_message': context.strip()
                })
        return messages[:10]  # cap at 10

    # ── Parsers ────────────────────────────────────────────────────

    def _parse_cells(self, cells: list[str]) -> dict | None:
        phone = service = message = timestamp = ''

        for cell in cells:
            if re.search(r'\+?\d{10,15}', cell):
                phone = self._clean_phone(cell)
            elif re.search(r'\d{1,2}[:/]\d{2}', cell) and len(cell) < 20:
                timestamp = cell
            elif len(cell) > 30:
                message = cell
            elif len(cell) > 3:
                service = cell

        otp = self._extract_otp(message) or self._extract_otp(' '.join(cells))
        if not otp:
            return None

        return {
            'otp': otp,
            'phone': phone or 'N/A',
            'service': self._clean_service(service) if service else self._find_service(message),
            'timestamp': timestamp or datetime.now().strftime('%H:%M:%S'),
            'raw_message': message
        }

    def _parse_raw_text(self, text: str) -> dict | None:
        otp = self._extract_otp(text)
        if not otp:
            return None
        return {
            'otp': otp,
            'phone': self._find_phone(text),
            'service': self._find_service(text),
            'timestamp': datetime.now().strftime('%H:%M:%S'),
            'raw_message': text[:300]
        }

    # ── Helpers ────────────────────────────────────────────────────

    def _extract_otp(self, text: str) -> str | None:
        if not text:
            return None
        patterns = [
            r'(?:otp|code|pin|verification)[:\s#-]*(\d{4,8})',
            r'\b(\d{6})\b',
            r'\b(\d{5})\b',
            r'\b(\d{4})\b',
            r'\b(\d{7,8})\b',
        ]
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                return m.group(1)
        return None

    def _find_phone(self, text: str) -> str:
        m = re.search(r'(\+?\d[\d\s\-]{9,14}\d)', text)
        return self._clean_phone(m.group(1)) if m else 'N/A'

    def _find_service(self, text: str) -> str:
        services = [
            'Facebook', 'Google', 'Instagram', 'Twitter', 'WhatsApp',
            'Telegram', 'Discord', 'TikTok', 'Snapchat', 'LinkedIn',
            'Amazon', 'Netflix', 'Uber', 'Lyft', 'PayPal', 'Apple',
            'Microsoft', 'Yahoo', 'Outlook', 'Steam'
        ]
        for s in services:
            if re.search(s, text, re.IGNORECASE):
                return s
        return 'Unknown'

    def _clean_phone(self, phone: str) -> str:
        cleaned = re.sub(r'[^\d+]', '', phone)
        if cleaned and not cleaned.startswith('+'):
            cleaned = '+' + cleaned
        return cleaned or 'N/A'

    def _clean_service(self, service: str) -> str:
        return service.strip().title() if service else 'Unknown'

    def test_connection(self) -> bool:
        try:
            r = self.session.get(self.BASE_URL, timeout=10)
            return r.status_code == 200
        except Exception:
            return False
