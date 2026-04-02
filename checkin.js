/**
 * vps8-checkin — 签到核心脚本
 * 
 * 用法:
 *   node checkin.js
 *
 * 环境变量:
 *   VPS8_COOKIES    — 完整的 Cookie 字符串（必填）
 *   VPS8_BASE_URL   — 网站地址，默认 https://vps8.zz.cd
 */

const https = require('https');

const BASE = process.env.VPS8_BASE_URL || 'https://vps8.zz.cd';
const RAW  = (process.env.VPS8_COOKIES || '').trim().replace(/[\r\n]/g, '');

if (!RAW) {
  console.error('❌ 未设置 VPS8_COOKIES 环境变量');
  process.exit(1);
}

// ── helpers ─────────────────────────────────────────────────────────────

function req(method, url, headers = {}, body = null) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const opts = {
      hostname: u.hostname,
      path: u.pathname + u.search,
      method,
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Origin': BASE,
        'Accept': 'application/json, text/html, */*',
        ...headers,
      },
    };
    if (body !== null && body !== undefined) {
      opts.headers['Content-Type'] = 'application/x-www-form-urlencoded';
      opts.headers['Content-Length'] = Buffer.byteLength(body);
    }

    const r = https.request(opts, res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({
        status: res.statusCode,
        headers: res.headers,
        body: d,
      }));
    });
    r.on('error', reject);
    r.setTimeout(15000, () => { r.destroy(new Error('请求超时')); });
    if (body !== null && body !== undefined) r.write(body);
    r.end();
  });
}

// ── main ────────────────────────────────────────────────────────────────

async function main() {
  console.log('🦞 VPS吧每日签到开始…');
  console.log('🌐 目标站点:', BASE);

  // Step 1: GET 签到页面 → 拿 CSRF token
  console.log('📄 获取签到页面…');
  const page = await req('GET', BASE + '/points/signin', {
    Cookie: RAW,
  });
  console.log('   状态码:', page.status);

  if (page.status >= 400) {
    console.error('❌ 页面请求失败，状态码', page.status);
    process.exit(1);
  }

  // 检查是否被踢到登录页（302 跳 /login）
  if (page.status === 302) {
    const loc = page.headers.location || '';
    if (loc.includes('/login')) {
      console.error('❌ Cookie 已过期（重定向到登录页），请重新导出并更新 VPS8_COOKIES');
      process.exit(1);
    }
  }

  // 如果状态码是 200 但内容包含 "Login to your account" 说明也需要重新登录
  if (page.body && page.body.includes('Login to your account')) {
    console.error('❌ Cookie 已过期（页面显示登录界面），请重新导出并更新 VPS8_COOKIES');
    process.exit(1);
  }

  // 检查是否已经签过
  if (page.body && page.body.includes('已签到')) {
    console.log('✅ 今天已经签到过了！');
    return;
  }

  // 提取 CSRF token（FOSSBilling 在签到表单里用的是 CSRFToken）
  let csrfToken = null;
  const formMatch = page.body.match(/name="CSRFToken"\s+value="([a-zA-Z0-9]+)"/);
  if (formMatch) {
    csrfToken = formMatch[1];
  }
  // fallback: 从 meta 标签取
  if (!csrfToken) {
    const metaMatch = page.body.match(/name="csrf-token"\s+content="([a-zA-Z0-9]+)"/);
    if (metaMatch) csrfToken = metaMatch[1];
  }

  if (!csrfToken) {
    console.error('❌ 无法提取 CSRF token，页面可能已变化');
    process.exit(1);
  }
  console.log('🔑 CSRF token 已获取');

  // Step 2: POST 签到 API
  // FOSSBilling 签到接口: POST /api/client/points/signin
  // 方式1: query string 传 CSRFToken
  // 方式2: body 传 CSRFToken
  console.log('🎯 提交签到…');

  const postData = `CSRFToken=${encodeURIComponent(csrfToken)}`;

  const result = await req('POST', BASE + '/api/client/points/signin', {
    Cookie: RAW,
    Referer: BASE + '/points/signin',
    Origin: BASE,
    'X-Requested-With': 'XMLHttpRequest',
  }, postData);

  console.log('   状态码:', result.status);

  // 判断结果
  if (result.status === 200 || result.status === 302) {
    let success = false;
    try {
      const json = JSON.parse(result.body);
      if (json.error && json.error.message) {
        const msg = json.error.message;
        console.log('   服务器消息:', msg);
        if (msg.includes('已签到') || msg.includes('already')) {
          console.log('✅ 签到确认（今天已签到）');
          success = true;
        }
      }
      if (json.result !== null && json.result !== undefined) {
        console.log('   返回数据:', JSON.stringify(json.result).slice(0, 200));
        success = true;
      }
    } catch (_) {
      // 非 JSON 返回
      if (result.body.includes('签到成功') || result.body.includes('已签到')) {
        success = true;
      }
    }

    if (success) {
      console.log('✅ 签到完成！');
      return;
    }

    // 302 重定向到签到页面一般也意味着成功
    if (result.status === 302) {
      console.log('✅ 签到请求已发送 (302)');
      return;
    }
  }

  // 如果失败，尝试另一种方法：用 query string 传 token
  console.log('🔄 尝试第二种签到方式…');
  const result2 = await req('POST', BASE + '/api/client/points/signin?CSRFToken=' + encodeURIComponent(csrfToken), {
    Cookie: RAW,
    Referer: BASE + '/points/signin',
    Origin: BASE,
    'X-Requested-With': 'XMLHttpRequest',
  }, null);

  console.log('   状态码:', result2.status);

  if (result2.status === 200 || result2.status === 302) {
    let success = false;
    try {
      const json = JSON.parse(result2.body);
      if (json.result !== null && json.result !== undefined) success = true;
      if (json.error && json.error.message && json.error.message.includes('已签到')) success = true;
    } catch (_) {
      if (result2.body && (result2.body.includes('签到成功') || result2.body.includes('已签到'))) success = true;
    }
    if (success) {
      console.log('✅ 签到完成！');
    } else {
      console.log('⚠️  结果不确定，请手动检查');
    }
  } else {
    console.log('❌ 签到请求失败，状态码', result2.status);
    try {
      const je = JSON.parse(result2.body);
      if (je.error) {
        console.log('   错误信息:', je.error.message);
        if (je.error.message.toLowerCase().includes('authentication') || je.error.code === 206) {
          console.log('   → Cookie 可能已过期，请重新导出');
        }
      }
    } catch (_) {}
    process.exit(1);
  }
}

main().catch(e => {
  console.error('💥 脚本执行出错:', e.message);
  process.exit(1);
});
