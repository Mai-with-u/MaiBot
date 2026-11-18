/* Shared safe Markdown rendering for exploration results. */
(() => {
  function explorationInline(target, text) {
    const pattern = /\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*|`([^`]+)`/g;
    let position = 0;
    for (const match of text.matchAll(pattern)) {
      target.append(document.createTextNode(text.slice(position, match.index)));
      let node;
      if (match[1]) {
        try {
          const url = new URL(match[2]);
          if (!["https:", "http:"].includes(url.protocol)) throw new Error("unsupported");
          node = document.createElement("a");
          node.href = url.href; node.target = "_blank"; node.rel = "noopener noreferrer";
          node.textContent = match[1];
        } catch { node = document.createTextNode(match[0]); }
      } else {
        node = document.createElement(match[3] ? "strong" : "code");
        node.textContent = match[3] || match[4];
      }
      target.append(node);
      position = match.index + match[0].length;
    }
    target.append(document.createTextNode(text.slice(position)));
  }
  function explorationMarkdown(text) {
    const body = document.createElement("article");
    body.className = "explore-result";
    let code = null, list = null, paragraph = null;
    for (const line of text.split(/\r?\n/)) {
      if (line.startsWith("```")) {
        if (code) code = null;
        else { const pre = document.createElement("pre"); code = document.createElement("code"); pre.append(code); body.append(pre); }
        list = paragraph = null;
      } else if (code) code.append(document.createTextNode(line + "\n"));
      else if (!line.trim()) list = paragraph = null;
      else {
        const heading = line.match(/^#{1,6}\s+(.+)/);
        const bullet = line.match(/^\s*(?:([-*])|\d+\.)\s+(.+)/);
        if (heading) { const h = document.createElement("h4"); explorationInline(h, heading[1]); body.append(h); list = paragraph = null; }
        else if (bullet) {
          const tag = bullet[1] ? "UL" : "OL";
          if (!list || list.tagName !== tag) { list = document.createElement(tag.toLowerCase()); body.append(list); }
          const li = document.createElement("li"); explorationInline(li, bullet[2]); list.append(li); paragraph = null;
        } else {
          if (!paragraph) { paragraph = document.createElement("p"); body.append(paragraph); }
          else paragraph.append(document.createTextNode("\n"));
          explorationInline(paragraph, line); list = null;
        }
      }
    }
    return body;
  }

  window.explorationMarkdown = explorationMarkdown;
  function element(tag, text, className) {
    const node = document.createElement(tag);
    node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function httpUrl(value) {
    try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) ? url.href : null; }
    catch { return null; }
  }
  function reference(label, value) {
    const row = element("div", label + "：", "product-reference");
    const url = httpUrl(value);
    const node = element(url ? "a" : "span", value);
    if (url) { node.href = url; node.target = "_blank"; node.rel = "noopener noreferrer"; }
    row.append(node);
    return row;
  }
  window.explorationProducts = item => {
    const body = element("div", "", "exploration-products");
    if (item.result_version !== 2 && !item.resources && !item.experiences) {
      body.append(element("div", "历史结果 · 尚未分类", "product-label"));
      if (item.result) body.append(explorationMarkdown(item.result));
      return body;
    }
    if (item.result) body.append(element("p", item.result, "product-overview"));
    const kinds = { text: "文本", image: "图片", audio: "音频", video: "视频", file: "文件" };
    const outcomes = { success: "成功", partial: "部分成功", failed: "失败" };
    const sources = { observation: "实际观察", user: "用户反馈", unverified: "尚未验证" };
    for (const [key, label] of [["resources", "资源"], ["experiences", "经验"]]) {
      const entries = item[key] || [];
      const section = element("section", "", "product-section");
      section.append(element("h3", `${label} · ${entries.length}`));
      if (!entries.length) section.append(element("p", `尚未记录${label}`, "product-empty"));
      for (const [index, entry] of entries.entries()) {
        const card = element("article", "", "product-card");
        card.append(element("span", key === "resources" ? kinds[entry.kind] : outcomes[entry.outcome], "product-label"), element("h4", entry.title));
        if (key === "resources") {
          card.append(element("p", entry.summary));
          if (entry.content) card.append(element("div", entry.content, "product-content"));
          if (entry.location) card.append(reference("资源地址", entry.location));
          if (entry.source) card.append(reference("来源", entry.source));
          else card.append(element("div", "来源未记录", "product-label"));
          const url = httpUrl(entry.location);
          if (url && ["image", "audio", "video"].includes(entry.kind)) {
            const preview = document.createElement("details");
            preview.append(element("summary", "预览媒体"));
            preview.addEventListener("toggle", () => {
              if (!preview.open || preview.querySelector("img,audio,video")) return;
              const media = document.createElement(entry.kind === "image" ? "img" : entry.kind);
              if (entry.kind === "image") { media.alt = entry.title; media.referrerPolicy = "no-referrer"; }
              else { media.controls = true; media.preload = "metadata"; }
              media.addEventListener("error", () => preview.append(element("p", "预览失败，可通过资源地址查看。")), { once: true });
              media.src = url; preview.append(media);
            });
            card.append(preview);
          }
        } else {
          const fields = document.createElement("dl");
          fields.append(element("dt", "适用条件"), element("dd", entry.conditions));
          fields.append(element("dt", "实际路径"));
          const path = document.createElement("ol"), steps = document.createElement("dd");
          for (const step of entry.path) path.append(element("li", step));
          steps.append(path); fields.append(steps);
          fields.append(element("dt", "结果"), element("dd", entry.result));
          fields.append(element("dt", `反馈 · ${sources[entry.feedback_source]}`), element("dd", entry.feedback));
          card.append(fields);
          const reference = item.experience_refs?.[index];
          const reuses = (item.reuse_records || []).filter(record => record.experience_ref === reference);
          if (reuses.length) {
            const history = document.createElement("details");
            const count = outcome => reuses.filter(record => record.report.outcome === outcome).length;
            history.append(element("summary", `复用 ${reuses.length} 次 · 成功 ${count("success")} / 部分 ${count("partial")} / 失败 ${count("failed")}`));
            for (const reuse of [...reuses].reverse()) {
              const report = reuse.report;
              const attempt = element("article", "", "reuse-attempt");
              attempt.append(element("h4", `${outcomes[report.outcome]} · ${report.title}`),
                element("div", new Date(reuse.created_at * 1000).toLocaleString(), "product-label"));
              const values = document.createElement("dl");
              for (const [label, value] of [["本次条件", report.conditions], ["实际路径", report.path.join(" → ")],
                ["实际结果", report.result], [`反馈 · ${sources[report.feedback_source]}`, report.feedback],
                ["已验证的完善", report.improvement], ["失败原因", report.failure_reason]]) {
                if (value) values.append(element("dt", label), element("dd", value));
              }
              attempt.append(values); history.append(attempt);
            }
            card.append(history);
          }
        }
        section.append(card);
      }
      body.append(section);
    }
    return body;
  };
})();
