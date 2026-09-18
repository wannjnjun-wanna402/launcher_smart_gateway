const http = require('http');
const https = require('https');
const url = require('url');

const TARGET_PORT = 8082;
const TARGET_HOST = '127.0.0.1';
const LISTEN_PORT = 8081;
const LISTEN_HOST = '0.0.0.0';

const server = http.createServer((req, res) => {
    const parsedUrl = url.parse(req.url, true);
    const path = parsedUrl.pathname;
    const isChatCompletions = (req.method === 'POST' && path === '/v1/chat/completions');
    const isCompletions = (req.method === 'POST' && path === '/v1/completions');
    const needsBodyFilter = isChatCompletions || isCompletions;

    let chunks = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => {
        let bodyBuffer = Buffer.concat(chunks);
        let bodyToSend = bodyBuffer;

        if (needsBodyFilter && bodyBuffer.length > 0) {
            try {
                const json = JSON.parse(bodyBuffer.toString('utf8'));
                if (json.grammar !== undefined) {
                    // CC Switch / Claude 转发的 grammar 经常导致 llama.cpp 报错
                    // 直接全部删除 grammar 字段，不影响正常对话
                    delete json.grammar;
                    bodyToSend = Buffer.from(JSON.stringify(json), 'utf8');
                }
                // 同时删除 response_format 的 json_schema（如果 llama.cpp 不支持）
                if (json.response_format && json.response_format.json_schema) {
                    delete json.response_format.json_schema;
                    // 如果 response_format 变空，也删除
                    if (Object.keys(json.response_format).length === 0) {
                        delete json.response_format;
                    }
                    bodyToSend = Buffer.from(JSON.stringify(json), 'utf8');
                }
            } catch (e) {
                // 解析失败，原样转发
            }
        }

        const options = {
            hostname: TARGET_HOST,
            port: TARGET_PORT,
            path: req.url,
            method: req.method,
            headers: { ...req.headers }
        };
        options.headers['host'] = `${TARGET_HOST}:${TARGET_PORT}`;
        // 修正 content-length 以匹配修改后的 body
        if (bodyToSend.length !== bodyBuffer.length) {
            options.headers['content-length'] = bodyToSend.length;
        }

        const proxyReq = http.request(options, (proxyRes) => {
            res.writeHead(proxyRes.statusCode, proxyRes.headers);
            proxyRes.pipe(res);
        });

        proxyReq.on('error', (err) => {
            res.writeHead(502, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: 'Proxy error: ' + err.message }));
        });

        proxyReq.write(bodyToSend);
        proxyReq.end();
    });
});

server.listen(LISTEN_PORT, LISTEN_HOST, () => {
    console.log(`API 过滤代理已启动: http://${LISTEN_HOST}:${LISTEN_PORT} -> http://${TARGET_HOST}:${TARGET_PORT}`);
    console.log('自动过滤非法 grammar / json_schema 字段');
});

server.on('error', (err) => {
    console.error('代理启动失败:', err.message);
    process.exit(1);
});
