/* SwingKid 멀티 agent 아키텍처 — 애플풍 SVG 다이어그램 + 모듈별 중요 코드 패널.
 *
 * 쓰는 곳: 메인 화면(index.html) 아래쪽 섹션, /architecture(크게 보기). 둘 다 SwingKidArch.mount(요소) 한 줄로 붙입니다.
 * 모듈(상자)을 누르면 /architecture/code/{모듈} 에서 실제 소스 파일의 해당 함수를 줄 번호와 함께 받아 보여 줍니다.
 * 보라색은 LLM·모델 호출(Bedrock Claude 답변 생성, Titan 임베딩)입니다.
 */
(() => {
  if (window.SwingKidArch) return;

  // ── 배치 (viewBox 1260 × 880, 데스크톱에서 거의 1:1 이라 글씨가 그대로 보임) ──
  const COL = { a: 30, b: 350, c: 680, d: 1000 };
  const W = { a: 240, b: 250, c: 250, d: 230 };
  const ROW = [40, 210, 380, 560, 740];
  const H = 104;

  const NODES = [
    { id: "ui", col: "a", row: 0, kind: "frontend", chip: "웹 화면", title: "SwingKid 웹 화면", sub: "질문 · 사진 · 영상 · GIF", file: "static/index.html" },
    { id: "api", col: "b", row: 0, kind: "api", chip: "API", title: "FastAPI 서버", sub: "POST /query · 입력 검사", file: "src/app.py" },
    { id: "guard", col: "b", row: 1, kind: "guard", chip: "가드레일", title: "가드레일", sub: "규칙 기반 · 항상 같은 결과", file: "src/guard.py" },
    { id: "exit", col: "a", row: 1, kind: "exit", chip: "즉시 응답", title: "고정 문구로 응답", sub: "차단(blocked) · 골프 무관", file: "LLM 호출 없음" },
    { id: "supervisor", col: "b", row: 2, kind: "router", chip: "라우터", title: "Supervisor", sub: "규칙 라우팅 · 웹 폴백", file: "src/agent.py" },
    { id: "memory", col: "a", row: 2, kind: "store", chip: "기억", title: "기억 · 훈련 저장소", sub: "대화방 · 판정 기록 · 연습 기록", file: "SqliteSaver · SqliteStore · training.py" },
    { id: "coach", col: "b", row: 3, kind: "coach", chip: "LLM 호출", title: "coach", sub: "근거로만 답변 작성", file: "src/agent.py · _coach" },
    { id: "claude", col: "b", row: 4, kind: "llm", chip: "LLM", title: "Bedrock · Claude", sub: "답변 생성 (Converse API)", file: "ChatBedrockConverse" },
    { id: "swing", col: "c", row: 1, kind: "agent", chip: "sub-agent", title: "swing_agent", sub: "사진 · 영상 · GIF 자세 판정", file: "src/subagents.py" },
    { id: "knowledge", col: "c", row: 2, kind: "agent", chip: "sub-agent", title: "knowledge_agent", sub: "골프 지식 문서 검색", file: "src/subagents.py" },
    { id: "info", col: "c", row: 3, kind: "agent", chip: "sub-agent", title: "info_agent", sub: "최신 · 문서 밖 정보", file: "src/subagents.py" },
    { id: "pose", col: "d", row: 1, kind: "tool", chip: "도구", title: "MediaPipe 포즈", sub: "관절 → 각도 판정", file: "src/tools.py" },
    { id: "docs", col: "d", row: 2, kind: "tool", chip: "RAG", title: "골프 지식 문서 25개", sub: "BM25 + Chroma 벡터", file: "src/retriever.py", h: 118, llmChip: "Titan 임베딩 · Bedrock" },
    { id: "mcp", col: "d", row: 3, kind: "tool", chip: "MCP 서버", title: "golf-web-search", sub: "stdio · golf_web_search 도구", file: "src/mcp_server.py" },
    { id: "ddg", col: "d", row: 4, kind: "external", chip: "외부", title: "DuckDuckGo", sub: "골프 웹 검색 (ddgs)", file: "src/web_search.py" },
  ];
  const box = (n) => ({ x: COL[n.col], y: ROW[n.row] - (n.h ? (n.h - H) / 2 : 0), w: W[n.col], h: n.h || H });

  // kind: main(요청 흐름) · tool(도구 호출) · llm(LLM·모델 호출) · reply(응답) / both: 호출 후 결과 반환
  const EDGES = [
    { d: "M270,92 L350,92", kind: "main", label: "/query", at: [310, 92] },
    { d: "M475,144 L475,210", kind: "main", label: "ask()", at: [475, 177] },
    { d: "M350,262 L270,262", kind: "tool", label: "차단 · 무관", at: [310, 262] },
    { d: "M150,210 L150,144", kind: "reply", label: "고정 문구", at: [150, 177] },
    { d: "M475,314 L475,380", kind: "main", label: "통과", at: [475, 347] },
    { d: "M350,432 L270,432", kind: "tool", label: "기억", at: [310, 432], both: true },
    { d: "M600,408 L640,408 L640,262 L680,262", kind: "main", label: "사진·영상·GIF", at: [640, 335], both: true },
    { d: "M600,432 L680,432", kind: "main", label: "골프 지식", at: [640, 432], both: true },
    { d: "M600,456 L640,456 L640,612 L680,612", kind: "main", label: "최신 · 폴백", at: [640, 535], both: true },
    { d: "M475,484 L475,560", kind: "main", label: "근거 전달", at: [475, 522] },
    { d: "M475,664 L475,740", kind: "llm", label: "LLM 호출 · invoke()", at: [475, 702] },
    { d: "M350,612 L14,612 L14,92 L30,92", kind: "reply", label: "응답 JSON", at: [190, 612] },
    { d: "M930,262 L1000,262", kind: "tool", label: "관절", at: [965, 262] },
    { d: "M930,432 L1000,432", kind: "tool", label: "검색", at: [965, 432] },
    { d: "M930,612 L1000,612", kind: "tool", label: "MCP", at: [965, 612] },
    { d: "M1115,664 L1115,740", kind: "tool", label: "웹 검색", at: [1115, 702] },
  ];

  const STEPS = [
    ["질문 받기", "웹 화면이 질문과 사진·영상·GIF, 대화방(<code>thread_id</code>)을 <code>POST /query</code>로 보냅니다. 로그인했으면 세션 쿠키로 장기 기억 주인을 정합니다."],
    ["가드레일", "의학·구매/가격·개인정보·프롬프트 인젝션은 바로 차단하고, 골프와 무관한 질문은 검색 없이 <code>no_answer</code>로 끝냅니다. LLM을 부르지 않습니다."],
    ["Supervisor 라우팅", "사진·영상·GIF는 <code>swing_agent</code>, 올해·순위·시세 같은 최신 정보는 <code>info_agent</code>, 그 밖의 골프 질문은 <code>knowledge_agent</code>로. '지난번 판정' 질문은 장기 기억으로, '요즘 연습 어때?' 질문은 코드가 집계한 연습 요약으로 바로 답합니다."],
    ["sub-agent가 근거 모으기", "자세는 MediaPipe 관절 → 각도로 판정(순수 함수), 지식은 문서 25개를 단어·의미 검색, 최신 정보는 <b>MCP 서버</b>의 <code>golf_web_search</code> 도구로 DuckDuckGo 검색. 결과를 들고 Supervisor로 돌아옵니다."],
    ["폴백과 근거 없음", "문서에 근거가 없으면 <code>info_agent</code>로 한 번 더. 자세 인식 실패나 웹에서도 근거가 없으면 LLM 없이 <code>no_answer</code>."],
    ["coach → LLM", "근거·판정 각도·직전 판정·이전 기록·연습 기록으로 프롬프트를 만들어 <b class=\"gka-llm-text\">Bedrock Claude를 한 번 호출</b>합니다. 근거 밖 내용과 측정하지 않은 수치는 만들지 않습니다."],
    ["응답과 화면", "<code>answer</code>·<code>contexts</code>·<code>trace</code>에 <code>status</code>·<code>issue_codes</code>·<code>sources</code>·<code>agents</code>·<code>angle_series</code>를 더해 돌려주고, 화면은 판정·각도 그래프·근거를 그립니다."],
    ["기억", "대화방별 단기 기억은 이어지는 질문('손목 스냅은요?')에 직전 판정을, 로그인 계정별 장기 기억은 기기를 넘어 판정 기록을 이어 줍니다. 훈련 화면의 계획·기록·달성률(코드 집계)도 같은 계정에 쌓여 코칭 근거가 됩니다."],
  ];

  const CSS = `
.gka { --gka-blue: var(--yellow, #0071e3); --gka-fg: var(--fg, #1d1d1f); --gka-muted: var(--muted, #6e6e73);
  --gka-surface: var(--surface, #fff); --gka-surface2: var(--surface-2, #f5f5f7); --gka-line: var(--line, rgb(0 0 0 / .08));
  --gka-gray: #8e8e93; --gka-purple: #7d5cf5; --gka-purple2: #bf5af2; --gka-purple-soft: #f1ecff; --gka-green: #34c759; --gka-red: #ff3b30; --gka-orange: #ff9500;
  --gka-font: var(--font, -apple-system, BlinkMacSystemFont, "SF Pro Text", "Apple SD Gothic Neo", "Noto Sans KR", system-ui, sans-serif);
  --gka-display: var(--font-display, -apple-system, BlinkMacSystemFont, "SF Pro Display", "Apple SD Gothic Neo", "Noto Sans KR", system-ui, sans-serif);
  --gka-mono: var(--mono, ui-monospace, "SF Mono", Menlo, Consolas, monospace);
  font-family: var(--gka-font); color: var(--gka-fg); letter-spacing: -.01em; }
@media (prefers-color-scheme: dark) { .gka { --gka-gray: #8e8e93; --gka-purple: #9d85ff; --gka-purple2: #d07cff; --gka-purple-soft: #2a2140; } }
.gka-canvas { position: relative; overflow-x: auto; border-radius: 20px; background:
  radial-gradient(circle at 1px 1px, var(--gka-line) 1px, transparent 0) 0 0 / 22px 22px, var(--gka-surface2); padding: 8px; }
.gka-canvas svg { display: block; width: 100%; min-width: 980px; height: auto; }
.gka-hint { display: none; font-size: 12px; color: var(--gka-muted); margin: 6px 2px 0; }
@media (max-width: 1000px) { .gka-hint { display: block; } }
.gka-node { cursor: pointer; outline: none; }
.gka-node .card { fill: var(--gka-surface); stroke: var(--gka-line); stroke-width: 1; transition: transform .25s var(--ease, ease), filter .25s; }
.gka-node:hover .card, .gka-node:focus-visible .card { stroke: var(--gka-blue); stroke-width: 2; }
.gka-node.sel .card { stroke: var(--gka-blue); stroke-width: 2.5; }
.gka-node .lift { transition: transform .25s cubic-bezier(.25,.1,.25,1); }
.gka-node:hover .lift, .gka-node:focus-visible .lift { transform: translateY(-3px); }
.gka-node .title { font: 650 18px var(--gka-display); letter-spacing: -.02em; fill: var(--gka-fg); }
.gka-node .sub { font: 400 14px var(--gka-font); fill: var(--gka-muted); }
.gka-node .file { font: 500 12px var(--gka-mono); fill: var(--gka-muted); }
.gka-node .chip-t { font: 650 11.5px var(--gka-font); letter-spacing: .01em; }
.gka-node .more { font: 600 12px var(--gka-font); fill: var(--gka-blue); opacity: 0; transition: opacity .2s; }
.gka-node:hover .more, .gka-node:focus-visible .more { opacity: 1; }
.gka-node.k-coach .card { stroke: var(--gka-purple); stroke-width: 2; }
.gka-node.k-llm .card { fill: url(#gka-llm-grad); stroke: none; }
.gka-node.k-llm .title, .gka-node.k-llm .sub, .gka-node.k-llm .file { fill: #fff; }
.gka-node.k-llm .more { fill: #fff; }
.gka-node.k-exit .card { fill: var(--gka-surface2); stroke-dasharray: 5 4; stroke: var(--gka-gray); }
.gka-edge { fill: none; stroke-linecap: round; stroke-linejoin: round; }
.gka-edge.main { stroke: var(--gka-blue); stroke-width: 2.5; }
.gka-edge.tool { stroke: var(--gka-gray); stroke-width: 2; }
.gka-edge.reply { stroke: var(--gka-blue); stroke-width: 2; stroke-dasharray: 7 6; opacity: .75; }
.gka-edge.llm { stroke: url(#gka-llm-grad-v); stroke-width: 4.5; stroke-dasharray: 10 8; animation: gka-flow 1.2s linear infinite; }
@keyframes gka-flow { to { stroke-dashoffset: -36; } }
@media (prefers-reduced-motion: reduce) { .gka-edge.llm { animation: none; } }
.gka-lbl rect { fill: var(--gka-surface); stroke: var(--gka-line); }
.gka-lbl text { font: 600 12.5px var(--gka-font); fill: var(--gka-fg); }
.gka-lbl.llm rect { fill: var(--gka-purple); stroke: none; }
.gka-lbl.llm text { fill: #fff; }
.gka-legend text { font: 500 13px var(--gka-font); fill: var(--gka-muted); }
.gka-legend .lt { font: 650 13px var(--gka-font); fill: var(--gka-fg); }
.gka-steps { list-style: none; counter-reset: gka; margin: 22px 0 0; padding: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.gka-steps li { counter-increment: gka; position: relative; min-width: 0; overflow-wrap: anywhere; padding: 14px 16px 14px 56px; background: var(--gka-surface2); border-radius: 18px; font-size: 14px; line-height: 1.6; }
.gka-steps li::before { content: counter(gka); position: absolute; left: 16px; top: 14px; width: 26px; height: 26px; border-radius: 50%; background: var(--gka-blue); color: #fff; font-weight: 700; font-size: 13px; display: grid; place-items: center; }
.gka-steps li.llm::before { background: linear-gradient(135deg, var(--gka-purple), var(--gka-purple2)); }
.gka-steps b.t { display: block; font: 650 15px var(--gka-display); margin-bottom: 2px; }
.gka-steps code { font: 12.5px var(--gka-mono); background: var(--gka-surface); border-radius: 6px; padding: 1px 5px; }
.gka-llm-text { color: var(--gka-purple); }
@media (max-width: 760px) { .gka-steps { grid-template-columns: minmax(0, 1fr); } }

/* 코드 패널 (오른쪽에서 나오는 시트, 모바일은 아래에서) */
.gka-scrim { position: fixed; inset: 0; background: rgb(0 0 0 / .28); opacity: 0; pointer-events: none; transition: opacity .3s; z-index: 80; }
.gka-scrim.open { opacity: 1; pointer-events: auto; }
.gka-sheet { position: fixed; top: 12px; right: 12px; bottom: 12px; width: min(720px, calc(100vw - 24px)); z-index: 81;
  background: var(--glass, rgb(255 255 255 / .86)); backdrop-filter: saturate(180%) blur(24px); -webkit-backdrop-filter: saturate(180%) blur(24px);
  border-radius: 24px; box-shadow: 0 20px 60px rgb(0 0 0 / .22); transform: translateX(calc(100% + 24px)); transition: transform .38s cubic-bezier(.32,.72,0,1);
  display: flex; flex-direction: column; overflow: hidden; font-family: var(--gka-font); color: var(--gka-fg); }
.gka-sheet.open { transform: none; }
@media (prefers-color-scheme: dark) { .gka-sheet { background: rgb(28 28 30 / .9); } }
@media (max-width: 760px) { .gka-sheet { top: auto; left: 8px; right: 8px; bottom: 8px; width: auto; height: 82vh; transform: translateY(calc(100% + 24px)); } }
.gka-sh-head { padding: 22px 24px 14px; border-bottom: 1px solid var(--gka-line); display: grid; grid-template-columns: 1fr auto; gap: 6px 12px; }
.gka-sh-head h3 { margin: 0; font: 700 24px var(--gka-display); letter-spacing: -.03em; }
.gka-sh-head p { grid-column: 1 / -1; margin: 0; color: var(--gka-muted); font-size: 14.5px; line-height: 1.6; }
.gka-sh-chip { display: inline-block; font: 650 12px var(--gka-font); padding: 3px 10px; border-radius: 999px; background: var(--yellow-soft, #e8f2fd); color: var(--gka-blue); margin-bottom: 6px; }
.gka-sh-chip.llm { background: linear-gradient(135deg, var(--gka-purple), var(--gka-purple2)); color: #fff; }
.gka-close { border: 0; width: 34px; height: 34px; border-radius: 50%; background: var(--gka-surface2); color: var(--gka-fg); font-size: 18px; cursor: pointer; }
.gka-close:hover { background: var(--gka-line); }
.gka-sh-body { overflow: auto; padding: 16px 20px 24px; display: flex; flex-direction: column; gap: 16px; }
.gka-snip { background: var(--gka-surface); border-radius: 16px; box-shadow: 0 1px 3px rgb(0 0 0 / .06); overflow: hidden; }
.gka-snip-h { display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap; padding: 10px 14px; font: 600 13px var(--gka-font); border-bottom: 1px solid var(--gka-line); }
.gka-snip-h .p { font-family: var(--gka-mono); font-weight: 500; color: var(--gka-muted); }
.gka-code { margin: 0; padding: 10px 0; overflow: auto; font: 13px/1.62 var(--gka-mono); tab-size: 4; }
.gka-code .ln { display: grid; grid-template-columns: 3.4em 1fr; }
.gka-code .ln > span:first-child { text-align: right; padding-right: 14px; color: var(--gka-muted); opacity: .6; user-select: none; }
.gka-code .ln > span:last-child { white-space: pre; padding-right: 16px; }
.gka-code .ln.hl { background: rgb(0 113 227 / .1); box-shadow: inset 3px 0 var(--gka-blue); }
.gka-code.llm .ln.hl { background: rgb(125 92 245 / .14); box-shadow: inset 3px 0 var(--gka-purple); }
.gka-code .kw { color: #ad3da4; } .gka-code .st { color: #c41a16; } .gka-code .cm { color: #707f8c; font-style: italic; } .gka-code .nu { color: #1c00cf; }
@media (prefers-color-scheme: dark) { .gka-code .kw { color: #ff7ab2; } .gka-code .st { color: #ff8170; } .gka-code .cm { color: #7f8c98; } .gka-code .nu { color: #d9c97c; } }
.gka-loading { color: var(--gka-muted); font-size: 14px; padding: 8px 4px; }
`;

  const NS = "http://www.w3.org/2000/svg";
  const s = (tag, attrs = {}, text) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text != null) e.textContent = text; return e; };
  const h = (tag, attrs = {}, html) => { const e = document.createElement(tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (html != null) e.innerHTML = html; return e; };
  const esc = (t) => t.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const textW = (t, px) => [...t].reduce((w, ch) => w + (/[ㄱ-힝]/.test(ch) ? px * 0.98 : /[A-Z0-9]/.test(ch) ? px * 0.64 : px * 0.56), 0);

  const CHIP = {
    frontend: ["#e8f2fd", "#0066cc"], api: ["#e8f2fd", "#0066cc"], guard: ["#fdecee", "#d70015"], exit: ["#ececf0", "#6e6e73"],
    router: ["#e3f5e8", "#248a3d"], store: ["#fff3e0", "#b25000"], coach: ["url(#gka-llm-grad)", "#fff"], llm: ["rgb(255 255 255 / .22)", "#fff"],
    agent: ["#e3f5e8", "#248a3d"], tool: ["#ececf0", "#3a3a3c"], external: ["#ececf0", "#3a3a3c"],
  };

  function diagram(onPick) {
    const svg = s("svg", { viewBox: "0 0 1260 880", role: "group", "aria-label": "SwingKid 멀티 agent 아키텍처. 모듈을 누르면 중요 코드를 볼 수 있어요" });
    const defs = s("defs");
    defs.innerHTML = `
      <linearGradient id="gka-llm-grad" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#5e5ce6"/><stop offset="1" stop-color="#bf5af2"/></linearGradient>
      <linearGradient id="gka-llm-grad-v" x1="0" y1="0" x2="0" y2="1" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#5e5ce6"/><stop offset="1" stop-color="#bf5af2"/></linearGradient>
      <filter id="gka-shadow" x="-10%" y="-10%" width="120%" height="140%"><feDropShadow dx="0" dy="6" stdDeviation="9" flood-color="#000" flood-opacity=".08"/></filter>
      <filter id="gka-glow" x="-30%" y="-30%" width="160%" height="170%"><feDropShadow dx="0" dy="8" stdDeviation="14" flood-color="#7d5cf5" flood-opacity=".38"/></filter>
      ${["main", "tool", "reply", "llm"].map((k) => `<marker id="gka-a-${k}" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="${k === "llm" ? 5 : 7}" markerHeight="${k === "llm" ? 5 : 7}" orient="auto-start-reverse"><path d="M1,1 L9,5 L1,9 z" fill="${{ main: "var(--gka-blue)", tool: "var(--gka-gray)", reply: "var(--gka-blue)", llm: "#bf5af2" }[k]}"/></marker>`).join("")}`;
    svg.append(defs);

    // 범례 (왼쪽 아래 빈 곳)
    const lg = s("g", { class: "gka-legend", transform: "translate(40 672)" });
    lg.append(s("text", { class: "lt", x: 0, y: 0 }, "읽는 법"));
    [["main", "요청 흐름"], ["tool", "도구 · 저장소 호출"], ["llm", "LLM · 모델 호출 (Bedrock)"], ["reply", "응답"]].forEach(([k, t], i) => {
      const y = 28 + i * 26;
      lg.append(s("path", { class: `gka-edge ${k}`, d: `M0,${y - 4} L40,${y - 4}`, "marker-end": `url(#gka-a-${k})` }), s("text", { x: 52, y }, t));
    });
    lg.append(s("text", { x: 0, y: 140 }, "⇄  호출한 뒤 결과를 들고 돌아옴"), s("text", { x: 0, y: 164 }, "모듈을 누르면 중요 코드가 열려요"));
    svg.append(lg);

    for (const e of EDGES) {
      svg.append(s("path", { class: `gka-edge ${e.kind}`, d: e.d, "marker-end": `url(#gka-a-${e.kind})`, ...(e.both ? { "marker-start": `url(#gka-a-${e.kind})` } : {}) }));
    }
    for (const e of EDGES) {
      const px = 12.5, w = textW(e.label, px) + 18, [x, y] = e.at;
      const g = s("g", { class: `gka-lbl ${e.kind === "llm" ? "llm" : ""}` });
      g.append(s("rect", { x: x - w / 2, y: y - 12, width: w, height: 24, rx: 12 }), s("text", { x, y: y + 4.5, "text-anchor": "middle" }, e.label));
      svg.append(g);
    }

    const nodes = {};
    for (const n of NODES) {
      const b = box(n);
      const g = s("g", { class: `gka-node k-${n.kind}`, tabindex: "0", role: "button", "aria-label": `${n.title} — 중요 코드 보기`, "data-id": n.id });
      const lift = s("g", { class: "lift" });
      lift.append(s("rect", { class: "card", x: b.x, y: b.y, width: b.w, height: b.h, rx: 18, filter: `url(#${n.kind === "llm" ? "gka-glow" : "gka-shadow"})` }));
      const [cbg, cfg] = CHIP[n.kind];
      const cw = textW(n.chip, 11.5) + 18;
      lift.append(s("rect", { x: b.x + 14, y: b.y + 12, width: cw, height: 20, rx: 10, fill: cbg }));
      lift.append(s("text", { class: "chip-t", x: b.x + 14 + cw / 2, y: b.y + 26, "text-anchor": "middle", fill: cfg }, (n.kind === "llm" || n.kind === "coach" ? "✦ " : "") + n.chip));
      lift.append(s("text", { class: "more", x: b.x + b.w - 14, y: b.y + 26, "text-anchor": "end" }, "코드 보기 ›"));
      lift.append(s("text", { class: "title", x: b.x + 16, y: b.y + 54 }, n.title));
      lift.append(s("text", { class: "sub", x: b.x + 16, y: b.y + 75 }, n.sub));
      if (n.llmChip) {
        const lw = textW("✦ " + n.llmChip, 11.5) + 18;
        lift.append(s("rect", { x: b.x + 16, y: b.y + 84, width: lw, height: 20, rx: 10, fill: "url(#gka-llm-grad)" }));
        lift.append(s("text", { class: "chip-t", x: b.x + 16 + lw / 2, y: b.y + 98, "text-anchor": "middle", fill: "#fff" }, "✦ " + n.llmChip));
      } else {
        lift.append(s("text", { class: "file", x: b.x + 16, y: b.y + b.h - 13 }, n.file));
      }
      g.append(lift);
      const pick = () => onPick(n, g);
      g.addEventListener("click", pick);
      g.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); } });
      nodes[n.id] = g;
      svg.append(g);
    }
    return svg;
  }

  // ── 코드 패널 ──
  const KW = /\b(def|class|return|if|elif|else|for|while|in|not|and|or|is|try|except|finally|raise|with|as|import|from|lambda|yield|async|await|None|True|False|self|const|let|var|function|new|of|typeof|catch|throw)\b/;
  function highlight(line) {
    const re = /("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`|#.*$|\/\/.*$|\b\d+(?:\.\d+)?\b|\b[A-Za-z_]\w*\b)/g;
    let out = "", last = 0, m;
    while ((m = re.exec(line))) {
      out += esc(line.slice(last, m.index));
      const t = m[0];
      if (t[0] === "#" || t.startsWith("//")) out += `<span class="cm">${esc(t)}</span>`;
      else if (/^["'`]/.test(t)) out += `<span class="st">${esc(t)}</span>`;
      else if (/^\d/.test(t)) out += `<span class="nu">${esc(t)}</span>`;
      else if (KW.test(t) && t.match(KW)[0] === t) out += `<span class="kw">${esc(t)}</span>`;
      else out += esc(t);
      last = m.index + t.length;
    }
    return out + esc(line.slice(last));
  }

  let sheet, scrim, lastFocus, selected;
  function ensureSheet() {
    if (sheet) return;
    scrim = h("div", { class: "gka-scrim" });
    sheet = h("aside", { class: "gka-sheet gka", role: "dialog", "aria-modal": "true", "aria-labelledby": "gka-sh-title" });
    scrim.addEventListener("click", closeSheet);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && sheet.classList.contains("open")) closeSheet(); });
    document.body.append(scrim, sheet);
  }
  function closeSheet() {
    sheet.classList.remove("open"); scrim.classList.remove("open");
    selected?.classList.remove("sel"); selected = null;
    lastFocus?.focus?.();
  }
  async function openSheet(n, g) {
    ensureSheet();
    lastFocus = g; selected?.classList.remove("sel"); selected = g; g.classList.add("sel");
    const isLlm = n.kind === "llm" || n.kind === "coach" || !!n.llmChip;
    sheet.innerHTML = "";
    const close = h("button", { class: "gka-close", type: "button", "aria-label": "닫기" }, "✕");
    close.addEventListener("click", closeSheet);
    const head = h("div", { class: "gka-sh-head" });
    head.append(h("div", {}, `<span class="gka-sh-chip ${isLlm ? "llm" : ""}">${isLlm ? "✦ " : ""}${esc(n.chip)}</span><h3 id="gka-sh-title">${esc(n.title)}</h3>`), close);
    const lead = h("p", {}, "불러오는 중…");
    head.append(lead);
    const body = h("div", { class: "gka-sh-body" });
    sheet.append(head, body);
    sheet.classList.add("open"); scrim.classList.add("open");
    close.focus();
    try {
      const res = await fetch(`/architecture/code/${n.id}`);
      if (!res.ok) throw new Error(`코드를 불러오지 못했어요 (${res.status})`);
      const d = await res.json();
      lead.textContent = d.summary;
      for (const sn of d.snippets) {
        const card = h("section", { class: "gka-snip" });
        card.append(h("div", { class: "gka-snip-h" }, `<span>${esc(sn.symbol)}</span><span class="p">${esc(sn.file)} · ${sn.start}–${sn.end}</span>`));
        const pre = h("pre", { class: `gka-code ${d.llm ? "llm" : ""}` });
        const marks = new Set(sn.highlight);
        pre.innerHTML = sn.code.split("\n").map((ln, i) => {
          const no = sn.start + i;
          return `<div class="ln${marks.has(no) ? " hl" : ""}"><span>${no}</span><span>${highlight(ln) || " "}</span></div>`;
        }).join("");
        card.append(pre);
        body.append(card);
      }
      const first = body.querySelector(".ln.hl");
      if (first) first.scrollIntoView({ block: "center" });
      body.scrollTop = Math.max(0, body.scrollTop - 40);
    } catch (err) {
      lead.textContent = err.message || String(err);
    }
  }

  function mount(root, { steps = true } = {}) {
    if (!document.getElementById("gka-css")) document.head.append(h("style", { id: "gka-css" }, CSS));
    root.classList.add("gka");
    const canvas = h("div", { class: "gka-canvas" });
    canvas.append(diagram(openSheet));
    root.append(canvas, h("p", { class: "gka-hint" }, "← 옆으로 밀어서 전체를 볼 수 있어요"));
    if (steps) {
      const ol = h("ol", { class: "gka-steps" });
      STEPS.forEach(([t, d], i) => ol.append(h("li", { class: i === 5 ? "llm" : "" }, `<b class="t">${t}</b>${d}`)));
      root.append(ol);
    }
  }

  window.SwingKidArch = { mount };
})();
