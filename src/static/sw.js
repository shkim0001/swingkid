// SwingKid 서비스 워커 — 웹앱 설치용 최소 구성
// - 화면 파일(/, 아이콘 등)만 저장해 두고, 인터넷이 끊겼을 때 첫 화면이라도 보여줍니다.
// - API 요청(/query, /auth, /memory ...)은 절대 저장하지 않고 항상 서버로 보냅니다.
const CACHE = "swingkid-v1";
const SHELL = ["/", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  // 페이지 이동만 처리: 항상 서버의 최신 화면을 먼저, 실패하면 저장해 둔 첫 화면
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).then((res) => {
        if (res.ok && new URL(req.url).pathname === "/") {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put("/", copy));
        }
        return res;
      }).catch(() => caches.match("/"))
    );
  }
  // 그 밖의 요청(API 포함)은 손대지 않고 브라우저 기본 동작대로 보냅니다.
});
