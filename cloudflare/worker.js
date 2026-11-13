// SwingKid 데모용 Worker
// - "/", "/architecture", "/arch.js" 같은 정적 파일은 Cloudflare 가 먼저 내려줍니다 (이 코드까지 오지 않음).
// - 정적 파일이 아닌 요청은 모두 이 Worker 로 와서, 내 PC 백엔드(BACKEND_URL)로 그대로 전달됩니다.
//   그래서 프론트 코드의 상대 경로 fetch("/query") 를 고칠 필요가 없고 CORS 도 필요 없습니다.

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // 백엔드는 화면 스크립트(arch.js, edu.js)를 /static/... 로 내려주므로 같은 경로를 정적 파일로 연결합니다.
    if (["/static/arch.js", "/static/edu.js"].includes(url.pathname)) {
      return env.ASSETS.fetch(new URL(url.pathname.slice("/static".length), url));
    }

    if (!env.BACKEND_URL) {
      return json(503, { error: "BACKEND_URL 이 설정되지 않았습니다. 터널 주소로 다시 배포해 주세요." });
    }

    const target = new URL(url.pathname + url.search, env.BACKEND_URL);
    const headers = new Headers(request.headers);
    headers.delete("host");
    // 백엔드가 Google 로그인 콜백 주소를 브라우저가 보는 도메인으로 만들 수 있게 원래 도메인을 넘깁니다.
    headers.set("x-forwarded-host", url.host);
    headers.set("x-forwarded-proto", url.protocol.replace(":", ""));

    try {
      return await fetch(target, {
        method: request.method,
        headers,
        body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
        redirect: "manual",
      });
    } catch (err) {
      return json(502, { error: "백엔드(내 PC)에 연결할 수 없습니다. 서버와 터널이 켜져 있는지 확인해 주세요.", detail: String(err) });
    }
  },
};

function json(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}
