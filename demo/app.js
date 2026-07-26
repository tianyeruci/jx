"use strict";

const app = {
  mode: "user",
  userView: "home",
  opsView: "publish",
  clarified: false,
  generated: {},
};

const phoneScreen = document.querySelector("#phone-screen");
const opsContent = document.querySelector("#ops-content");
const toast = document.querySelector("#toast");
const dialog = document.querySelector("#demo-dialog");
const dialogContent = document.querySelector("#dialog-content");

const courses = [
  { id: "course_ai_001", name: "AI工具入门", direction: "AI应用", clicks: 268, uv: 194 },
  { id: "course_ai_002", name: "AI办公效率", direction: "AI应用", clicks: 213, uv: 157 },
  { id: "course_business_001", name: "商业问题分析", direction: "商业基础", clicks: 176, uv: 131 },
  { id: "course_wechat_001", name: "公众号定位基础", direction: "公众号运营", clicks: 142, uv: 109 },
];

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("is-visible");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("is-visible"), 2400);
}

function setMode(mode) {
  app.mode = mode;
  document.querySelectorAll(".mode-button").forEach((button) => {
    const active = button.dataset.mode === mode;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  document.querySelector("[data-stage='user']").hidden = mode !== "user";
  document.querySelector("[data-stage='ops']").hidden = mode !== "ops";
  if (mode === "ops") renderOps();
}

function mobileHeader(title, back = false) {
  return `<header class="mobile-header">
    <button class="icon-button" type="button" data-nav="${back ? "home" : "history"}" aria-label="${back ? "返回首页" : "查看历史"}">${back ? "←" : "☰"}</button>
    <h2>${title}</h2>
    <button class="icon-button" type="button" data-action="notice" aria-label="查看提醒">◌</button>
  </header>`;
}

function renderHome() {
  phoneScreen.innerHTML = `
    <section class="welcome">
      <p class="date">MONDAY · 27 JULY</p>
      <h2>今天，先解决<br><span>哪一件事？</span></h2>
      <p>选择入口后，我只会使用完成这件事所需的信息。</p>
    </section>
    <section class="screen-pad">
      <p class="section-label">三个 AI 入口</p>
      <div class="entry-list">
        <button type="button" class="entry-card" data-nav="business"><span class="entry-icon">策</span><span><strong>商业顾问</strong><small>梳理问题、课程与学习路线</small></span><span class="arrow">→</span></button>
        <button type="button" class="entry-card" data-nav="moments"><span class="entry-icon">圈</span><span><strong>朋友圈文案</strong><small>把一段素材改成可发布文案</small></span><span class="arrow">→</span></button>
        <button type="button" class="entry-card" data-nav="writing"><span class="entry-icon">写</span><span><strong>文章创作</strong><small>从主题与素材生成文章草稿</small></span><span class="arrow">→</span></button>
      </div>
      <p class="privacy-note">商业顾问可在七天内续聊；朋友圈文案和文章创作不会保存历史。</p>
    </section>`;
}

function renderBusiness() {
  const outcome = app.clarified ? `
    <article class="message user">我刚开始，希望先学会把 AI 用在日常工作里。</article>
    <article class="message system">正在核对审核后的课程目录，并为你生成学习路线……</article>
    <article class="message">你更适合从基础用法开始，再进入办公场景和真实业务任务。下面这条路线来自已审核资料。</article>
    <article class="route-card">
      <div class="route-head"><span>正式学习路线</span><small>共 3 阶段</small></div>
      <h4>AI基础应用路线</h4>
      <div class="route-track"><div class="route-stage active">入门<br>现在</div><div class="route-stage">进阶<br>下一步</div><div class="route-stage">实战<br>目标</div></div>
    </article>
    <article class="course-card"><span class="course-symbol">▶</span><span><strong>AI工具入门</strong><small>入门 · 提问方法与事实核验</small></span><button type="button" data-course="course_ai_001" aria-label="打开 AI工具入门 测试链接">→</button></article>
    <article class="course-card"><span class="course-symbol">◇</span><span><strong>AI办公效率</strong><small>进阶 · 文档处理与工作流拆解</small></span><button type="button" data-course="course_ai_002" aria-label="打开 AI办公效率 测试链接">→</button></article>
    <article class="insight-card"><strong>灯泡　专业见解</strong>先用一周完成三个小任务：整理一次资料、改写一份文本、验证一次回答。能稳定复用后，再进入办公效率课程；不要用“打开链接”代替真正完成学习。</article>
    <div class="feedback"><span>这条建议对你有帮助吗？</span><span><button type="button" data-feedback="up" aria-label="有帮助">＋</button><button type="button" data-feedback="down" aria-label="没帮助">−</button></span></div>` : `
    <article class="message">我可以帮你梳理问题，也可以从已审核的课程里给出建议。你目前想达成什么目标？</article>
    <article class="message user">我想系统学习 AI 工具，应该从哪里开始？</article>
    <article class="message">先确认两个信息：你现在的使用程度，以及最想先解决的工作场景。</article>
    <div class="quick-replies" aria-label="快捷回答"><button type="button" data-clarify="beginner">刚开始，用在日常工作</button><button type="button" data-clarify="experienced">用过，想做业务实战</button></div>`;

  phoneScreen.innerHTML = `<section class="chat-screen">
    ${mobileHeader("觉醒 AI 指南", true)}
    <div class="chat-hero"><div class="mini-beacon">醒</div><h3>商业顾问</h3><p>会话历史保留 7 天 · 课程均经状态校验</p></div>
    <div class="timeline">${outcome}</div>
    <form class="composer" data-composer><input aria-label="输入问题" placeholder="继续问这个问题…"><button type="submit" aria-label="发送">➤</button></form>
  </section>`;
  phoneScreen.scrollTop = app.clarified ? phoneScreen.scrollHeight : 0;
}

function renderTool(type) {
  const isMoments = type === "moments";
  const result = app.generated[type];
  const title = isMoments ? "朋友圈文案" : "文章创作";
  phoneScreen.innerHTML = `
    ${mobileHeader(title, true)}
    <section class="tool-intro"><h2>${isMoments ? "把素材，变成可发布的话。" : "从一个明确主题，写出第一版。"}</h2><p>${isMoments ? "适合活动记录、经验分享与课程内容转述。" : "先给读者、目标与素材，系统再生成结构完整的草稿。"}</p></section>
    <form class="tool-form" data-tool-form="${type}">
      <div class="field"><label for="tool-request">这次要写什么</label><input id="tool-request" name="request" value="${isMoments ? "分享第一次用 AI 整理会议资料的体会" : "写一篇给新手看的 AI 输出核验方法"}" required></div>
      <div class="field"><label for="tool-audience">写给谁看</label><input id="tool-audience" name="audience" value="刚开始接触 AI 的职场人"></div>
      <div class="field"><label for="tool-tone">表达方式</label><select id="tool-tone" name="tone"><option>真诚、具体</option><option>专业、克制</option><option>轻松、有行动感</option></select></div>
      <button class="primary-button" type="submit">生成${isMoments ? "文案" : "草稿"}</button>
    </form>
    ${result ? `<article class="result-paper"><small>生成结果 · 可继续编辑</small><p>${result}</p></article>` : ""}
    <p class="no-history">本入口不保存历史；离开页面后内容会清空。</p>`;
}

function renderHistory() {
  phoneScreen.innerHTML = `${mobileHeader("最近会话", true)}<section class="screen-pad">
    <p class="section-label">七天内可继续</p>
    <div class="history-list">
      <button class="history-item" type="button" data-resume><strong>AI 学习路线怎么开始</strong><span>今天 09:32 · 已生成会话摘要</span></button>
      <button class="history-item" type="button" data-resume><strong>公众号定位需要哪些证据</strong><span>昨天 20:18 · 6 条消息</span></button>
      <button class="history-item" type="button" data-resume><strong>用户访谈的问题设计</strong><span>7 月 23 日 · 12 条消息</span></button>
    </div>
    <p class="privacy-note">超过 7 天的原始消息会在摘要成功后清理。朋友圈文案和文章创作不会出现在这里。</p>
  </section>`;
}

function renderCourses() {
  phoneScreen.innerHTML = `${mobileHeader("课程", true)}<section class="screen-pad"><p class="section-label">演示用审核课程</p><div class="entry-list">
    ${courses.map((course) => `<button type="button" class="entry-card" data-course="${course.id}"><span class="entry-icon">课</span><span><strong>${course.name}</strong><small>${course.direction} · test_only</small></span><span class="arrow">→</span></button>`).join("")}
  </div><p class="privacy-note">所有链接均为 example.com 测试地址，不代表正式课程。</p></section>`;
}

function renderState(type) {
  const error = type === "error";
  phoneScreen.innerHTML = `${mobileHeader(error ? "暂时无法连接" : "最近会话", true)}<section class="${error ? "error-state" : "empty-state"}">
    <div class="state-graphic">${error ? "!" : "醒"}</div><h3>${error ? "这次回答没有生成完成" : "还没有商业顾问会话"}</h3>
    <p>${error ? "网络连接中断，已保留你刚才的问题。可以重新生成，不会重复创建学习进度。" : "提出一个正在面对的具体问题，第一段可续聊的记录会从这里开始。"}</p>
    <button type="button" class="primary-button" data-nav="${error ? "business" : "home"}">${error ? "重新生成" : "开始第一次对话"}</button>
  </section>`;
}

function renderUser(view = app.userView) {
  app.userView = view;
  document.querySelectorAll("[data-nav]").forEach((button) => button.classList.toggle("is-active", button.dataset.nav === view));
  document.querySelectorAll(".step-button").forEach((button) => button.classList.toggle("is-active", button.dataset.nav === view));
  ({ home: renderHome, business: renderBusiness, moments: () => renderTool("moments"), writing: () => renderTool("writing"), history: renderHistory, courses: renderCourses, empty: () => renderState("empty"), error: () => renderState("error") }[view] || renderHome)();
}

function opsHeader(title, description, actions = "") {
  return `<header class="ops-heading"><div><p class="eyebrow">TEST_ONLY WORKFLOW</p><h1>${title}</h1><p>${description}</p></div><div class="ops-actions">${actions}</div></header>`;
}

function renderPublish() {
  opsContent.innerHTML = `${opsHeader("课程校验与发布", "首版通过 CLI 完成 dry-run 与 publish。此处只预览命令结果，不提供真实课程管理后台。", '<button type="button" class="secondary-button" data-action="dry-run">运行校验预览</button><button type="button" class="primary-button" data-action="publish-preview">预览发布</button>')}
  <div class="ops-grid"><section class="panel"><h3>合成测试种子 v1</h3><p class="panel-caption">test_data/courses/测试课程与路线.md</p><div class="check-list">
    <div class="check-row"><span class="check">✓</span><span>课程 ID、正式链接字段与审核状态</span><span class="tag">9 门通过</span></div>
    <div class="check-row"><span class="check">✓</span><span>路线阶段顺序与课程引用</span><span class="tag">2 条通过</span></div>
    <div class="check-row"><span class="check">✓</span><span>停用课程不生成推荐卡片</span><span class="tag warn">1 门停用</span></div>
    <div class="check-row"><span class="check">✓</span><span>所有示例链接保持 example.com</span><span class="tag">test_only</span></div>
  </div><div class="code-box">$ python -m ai_management.cli courses dry-run --source test_data/courses/测试课程与路线.md\n\nresult: valid · changes_not_applied: true</div></section>
  <aside class="panel"><h3>待发布摘要</h3><div class="metric-list"><div class="metric"><span>课程</span><strong>9</strong></div><div class="metric"><span>学习路线</span><strong>2</strong></div><div class="metric"><span>阻断错误</span><strong>0</strong></div><div class="metric"><span>警告</span><strong>1</strong></div></div></aside></div>`;
}

function renderCleaning() {
  opsContent.innerHTML = `${opsHeader("知识清洗批次", "查看上传、解析、清洗、Dify 索引与补偿阶段。失败文件可单独重试。", '<button type="button" class="primary-button" data-action="retry-clean">重试失败文件</button>')}
  <div class="filter-row"><select aria-label="批次状态"><option>全部状态</option><option>处理中</option><option>失败</option><option>完成</option></select><input aria-label="搜索批次" placeholder="批次 ID / 文件名"><button class="secondary-button" type="button" data-action="filter">筛选</button></div>
  <div class="table-wrap"><table class="data-table"><thead><tr><th>批次</th><th>文件</th><th>阶段</th><th>进度</th><th>状态</th><th>操作</th></tr></thead><tbody>
    <tr><td>clean_0727_01</td><td>公众号文章样本.docx</td><td>Dify 索引</td><td><div class="progress"><span style="width:72%"></span></div></td><td><span class="tag warn">处理中</span></td><td><button data-action="detail">详情</button></td></tr>
    <tr><td>clean_0727_02</td><td>课程文字稿样本.pdf</td><td>文件头校验</td><td><div class="progress"><span style="width:18%"></span></div></td><td><span class="tag error">失败</span></td><td><button data-action="detail-error">错误详情</button></td></tr>
    <tr><td>clean_0726_04</td><td>SOP_合成样本.md</td><td>完成</td><td><div class="progress"><span style="width:100%"></span></div></td><td><span class="tag">已完成</span></td><td><button data-action="detail">详情</button></td></tr>
  </tbody></table></div>`;
}

function renderLogs() {
  opsContent.innerHTML = `${opsHeader("必要操作与错误日志", "只保留必要操作、错误、任务阶段与关联 ID；不展示用户完整问题、回答、真实身份或密钥。")}
  <div class="filter-row"><select aria-label="日志等级"><option>全部等级</option><option>ERROR</option><option>WARNING</option><option>INFO</option></select><select aria-label="日志组件"><option>全部组件</option><option>cleaning</option><option>courses</option><option>conversation</option></select><input aria-label="请求 ID" placeholder="request ID"><button class="secondary-button" type="button" data-action="filter">筛选</button></div>
  <div class="table-wrap"><table class="data-table"><thead><tr><th>时间</th><th>等级</th><th>组件 / 事件</th><th>状态</th><th>关联 ID</th><th>详情</th></tr></thead><tbody>
    <tr><td>09:42:18</td><td><span class="tag error">ERROR</span></td><td>cleaning / FILE_SIGNATURE_INVALID</td><td>失败</td><td>clean_0727_02</td><td><button data-log="signature">查看</button></td></tr>
    <tr><td>09:31:04</td><td><span class="tag">INFO</span></td><td>courses / CATALOG_DRY_RUN</td><td>完成</td><td>publish_0727_01</td><td><button data-log="publish">查看</button></td></tr>
    <tr><td>09:18:51</td><td><span class="tag warn">WARN</span></td><td>conversation / SUMMARY_RETRY</td><td>待重试</td><td>conv_…7fa2</td><td><button data-log="summary">查看</button></td></tr>
  </tbody></table></div>`;
}

function renderClicks() {
  const days = [84, 112, 96, 131, 148, 123, 176];
  opsContent.innerHTML = `${opsHeader("课程点击次数", "PV 是课程链接被点击的总次数；UV 是补充的匿名点击人数。点击不代表已完成课程。")}
  <div class="ops-grid"><section class="panel"><h3>最近 7 天点击趋势</h3><p class="panel-caption">包含今天 Redis 中尚未落库的实时计数</p><div class="bars">${days.map((n,i) => `<div class="bar ${i === 6 ? "today" : ""}" style="height:${Math.round(n/1.9)}%"><span>${n}</span><small>7/${21+i}</small></div>`).join("")}</div></section>
  <aside class="panel"><h3>所选区间</h3><div class="metric-list"><div class="metric"><span>点击总次数 PV</span><strong>870</strong></div><div class="metric"><span>匿名点击人数 UV</span><strong>631</strong></div><div class="metric"><span>今日实时点击</span><strong>176</strong></div></div></aside></div>
  <div class="table-wrap"><table class="data-table"><thead><tr><th>课程</th><th>方向</th><th>点击次数 PV</th><th>点击人数 UV</th><th>数据状态</th></tr></thead><tbody>${courses.map((course, i) => `<tr><td>${course.name}</td><td>${course.direction}</td><td><strong>${course.clicks}</strong></td><td>${course.uv}</td><td><span class="tag ${i === 0 ? "warn" : ""}">${i === 0 ? "含实时" : "已落库"}</span></td></tr>`).join("")}</tbody></table></div>`;
}

function renderOps(view = app.opsView) {
  app.opsView = view;
  document.querySelectorAll("[data-ops]").forEach((button) => button.classList.toggle("is-active", button.dataset.ops === view));
  ({ publish: renderPublish, cleaning: renderCleaning, logs: renderLogs, clicks: renderClicks }[view] || renderPublish)();
}

function openDialog(title, body, details = null) {
  dialogContent.innerHTML = `<span class="dialog-kicker">STATIC DEMO / 模拟详情</span><h2>${title}</h2><p>${body}</p>${details ? `<dl class="detail-grid">${details.map(([key,value]) => `<dt>${key}</dt><dd>${value}</dd>`).join("")}</dl>` : ""}`;
  dialog.showModal();
}

document.addEventListener("click", (event) => {
  const mode = event.target.closest("[data-mode]");
  if (mode) return setMode(mode.dataset.mode);

  const nav = event.target.closest("[data-nav]");
  if (nav) return renderUser(nav.dataset.nav);

  const state = event.target.closest("[data-state]");
  if (state) return renderUser(state.dataset.state);

  const ops = event.target.closest("[data-ops]");
  if (ops) return renderOps(ops.dataset.ops);

  const clarify = event.target.closest("[data-clarify]");
  if (clarify) { app.clarified = true; renderBusiness(); showToast("已根据你的回答生成审核路线"); return; }

  const course = event.target.closest("[data-course]");
  if (course) {
    const found = courses.find((item) => item.id === course.dataset.course);
    openDialog(found ? found.name : "测试课程", "真实产品会先记录一次课程链接点击，再跳转到审核后的正式地址。本 Demo 不打开外部链接。", [["课程 ID", course.dataset.course], ["测试链接", `https://example.com/courses/${course.dataset.course}`], ["数据说明", "test_only · 不计入真实点击"]]);
    return;
  }

  const feedback = event.target.closest("[data-feedback]");
  if (feedback) return showToast(feedback.dataset.feedback === "up" ? "已记录：这条建议有帮助" : "已记录反馈，可继续说明哪里不合适");

  if (event.target.closest("[data-resume]")) { app.clarified = true; renderUser("business"); showToast("已载入会话摘要，可以继续追问"); return; }

  const action = event.target.closest("[data-action]");
  if (action) {
    const actions = {
      notice: () => showToast("没有新的系统提醒"),
      "dry-run": () => showToast("校验预览完成：9 门课程、2 条路线、0 个错误"),
      "publish-preview": () => openDialog("发布前最终确认", "这是静态流程预览，不会写入数据库或 Dify。真实发布由 CLI 执行，并保存结构化校验结果。", [["新增/更新", "9 门课程、2 条路线"], ["待停用", "旧版AI速成课"], ["目标", "隔离测试知识库"]]),
      "retry-clean": () => showToast("已模拟加入重试队列，不会上传文件"),
      filter: () => showToast("已应用演示筛选条件"),
      detail: () => openDialog("清洗任务详情", "每一步均有状态、耗时和失败阶段；索引成功且分块非零后才算完成。", [["策略", "test_markdown"], ["分块", "8"], ["敏感内容", "未记录"]]),
      "detail-error": () => openDialog("文件头校验失败", "文件扩展名是 PDF，但文件头不是 %PDF。文件未进入清洗和 Dify 上传阶段。", [["事件代码", "FILE_SIGNATURE_INVALID"], ["可重试", "替换正确文件后可重试"], ["request ID", "req_…4e21"]]),
    };
    (actions[action.dataset.action] || (() => showToast("这是静态流程预览")))();
    return;
  }

  const log = event.target.closest("[data-log]");
  if (log) {
    const data = {
      signature: ["错误事件详情", "必要诊断信息已脱敏；原始文件名和内容不会记录。", [["等级", "ERROR"], ["事件代码", "FILE_SIGNATURE_INVALID"], ["组件", "cleaning"], ["request ID", "req_…4e21"], ["处理建议", "替换与扩展名一致的 PDF 后重试"]]],
      publish: ["操作日志详情", "课程 dry-run 已结束，没有向 Dify 或数据库写入数据。", [["状态", "completed"], ["版本", "test-seed-v1"], ["耗时", "1.84s"]]],
      summary: ["摘要重试详情", "上一次 Workflow 调用超时。原始消息仍保留，游标未推进。", [["状态", "retry_scheduled"], ["重试次数", "1 / 3"], ["下次执行", "09:23:51"]]],
    }[log.dataset.log];
    openDialog(...data);
  }
});

document.addEventListener("submit", (event) => {
  if (event.target.matches("[data-composer]")) {
    event.preventDefault();
    const input = event.target.querySelector("input");
    if (!input.value.trim()) return;
    app.clarified = true;
    renderBusiness();
    showToast("已模拟发送问题");
  }
  if (event.target.matches("[data-tool-form]")) {
    event.preventDefault();
    const type = event.target.dataset.toolForm;
    app.generated[type] = type === "moments"
      ? "今天第一次让 AI 帮我整理会议资料。\n\n真正省时间的不是“一键生成”，而是先说清楚：要保留哪些事实、给谁看、下一步要做什么。AI 给了第一版，我再逐条核对，最后把两小时的散乱记录变成了一页行动清单。\n\n工具很快，判断仍然要自己做。"
      : "核验 AI 输出，不需要从一套复杂流程开始。\n\n先标出答案里的事实、数字和关键判断，再分别追问来源。能回到原始资料的内容继续使用；只有结论、没有依据的内容先放下。最后，用一个真实的小任务验证建议是否可行。\n\nAI 可以帮你更快形成第一版，但最终交付前的判断责任仍然在人。";
    renderTool(type);
    showToast(type === "moments" ? "朋友圈文案已生成" : "文章草稿已生成");
  }
});

renderUser("home");
