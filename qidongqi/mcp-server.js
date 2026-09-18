#!/usr/bin/env node
/**
 * MCP Server — 为 llama.cpp 提供 MCP 协议桥接
 * 
 * 监听端口: 50086
 * 后端 API: http://127.0.0.1:8081/v1
 * 
 * 符合 MCP (Model Context Protocol) 标准，支持:
 * - tools/list (列出可用工具)
 * - tools/call (调用工具 = 调用 AI)
 * - SSE (Server-Sent Events) 流式响应
 * 
 * 用法: node mcp-server.js [--port 50086] [--endpoint http://127.0.0.1:8081/v1]
 *       --mcp-key xxx   (MCP 访问 Key，空则不校验)
 */

const http = require('http');
const https = require('https');

// ─── 参数解析 ───
const args = process.argv.slice(2);
const getArg = (name, def) => { const i = args.indexOf(name); return i >= 0 && i + 1 < args.length ? args[i + 1] : def; };
const PORT = parseInt(getArg('--port', '50086'));
const LLAMA_ENDPOINT = getArg('--endpoint', 'http://127.0.0.1:8081/v1');
const MCP_KEY = getArg('--mcp-key', '');

// ─── 模型列表缓存 ───
let modelList = ['llama-model'];  // 默认占位，启动后自动刷新
let modelListLastFetch = 0;

// ─── HTTP 工具 ───
function httpRequest(url, options, body) {
    return new Promise((resolve, reject) => {
        const client = url.startsWith('https') ? https : http;
        const parsed = new URL(url);
        const opts = {
            hostname: parsed.hostname,
            port: parsed.port || (url.startsWith('https') ? 443 : 80),
            path: parsed.pathname + parsed.search,
            method: options.method || 'GET',
            headers: options.headers || {},
            timeout: 30000,
        };
        const req = client.request(opts, (res) => {
            let data = '';
            res.on('data', (chunk) => data += chunk);
            res.on('end', () => {
                try { resolve({ status: res.statusCode, data: JSON.parse(data), headers: res.headers }); }
                catch { resolve({ status: res.statusCode, data: data, headers: res.headers }); }
            });
        });
        req.on('error', (e) => reject(e));
        req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
        if (body) req.write(typeof body === 'string' ? body : JSON.stringify(body));
        req.end();
    });
}

// ─── 获取模型列表 ───
async function fetchModels() {
    try {
        const res = await httpRequest(`${LLAMA_ENDPOINT}/models`, { method: 'GET' });
        if (res.status === 200 && res.data && res.data.data) {
            modelList = res.data.data.map(m => m.id || m.name || 'unknown');
        }
        modelListLastFetch = Date.now();
    } catch (e) {
        // 静默失败，保留旧列表
    }
}

// ─── 调用 AI ───
async function callLLM(messages, model, stream = false) {
    const body = {
        model: model || modelList[0],
        messages: messages,
        stream: stream,
        max_tokens: 8192,
    };
    return await httpRequest(`${LLAMA_ENDPOINT}/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
    }, body);
}

// ─── 解析 MCP JSON-RPC 请求 ───
function handleMCPRequest(body) {
    const { id, method, params } = body;

    switch (method) {
        // ── 初始化 ──
        case 'initialize':
            return {
                jsonrpc: '2.0',
                id,
                result: {
                    protocolVersion: '2024-11-05',
                    capabilities: {
                        tools: {},
                        resources: {},
                        prompts: {},
                    },
                    serverInfo: {
                        name: 'llama-mcp-server',
                        version: '1.0.0',
                    },
                },
            };

        // ── 列出工具 ──
        case 'tools/list':
            return {
                jsonrpc: '2.0',
                id,
                result: {
                    tools: [
                        {
                            name: 'chat',
                            description: '与 AI 模型对话，支持文本生成、代码编写、分析等任务',
                            inputSchema: {
                                type: 'object',
                                properties: {
                                    messages: {
                                        type: 'array',
                                        description: '对话消息列表',
                                        items: {
                                            type: 'object',
                                            properties: {
                                                role: { type: 'string', enum: ['system', 'user', 'assistant'] },
                                                content: { type: 'string' },
                                            },
                                            required: ['role', 'content'],
                                        },
                                    },
                                    model: {
                                        type: 'string',
                                        description: '模型名称（可选，默认使用当前加载的模型）',
                                    },
                                    system: {
                                        type: 'string',
                                        description: '系统提示词',
                                    },
                                },
                                required: ['messages'],
                            },
                        },
                    ],
                },
            };

        // ── 调用工具 ──
        case 'tools/call':
            return handleToolCall(id, params);

        // ── 列出资源 ──
        case 'resources/list':
            return {
                jsonrpc: '2.0',
                id,
                result: { resources: [] },
            };

        // ── 列出提示词模板 ──
        case 'prompts/list':
            return {
                jsonrpc: '2.0',
                id,
                result: { prompts: [] },
            };

        // ── 未知方法 ──
        default:
            return {
                jsonrpc: '2.0',
                id,
                error: { code: -32601, message: `Method not found: ${method}` },
            };
    }
}

// ─── 处理工具调用 ───
async function handleToolCall(id, params) {
    const { name, arguments: args } = params;

    if (name === 'chat') {
        try {
            const messages = args.messages || [];
            const system = args.system || '';
            const model = args.model || null;

            if (system) {
                messages.unshift({ role: 'system', content: system });
            }

            const result = await callLLM(messages, model, false);

            if (result.status === 200 && result.data && result.data.choices) {
                const content = result.data.choices[0].message.content;
                return {
                    jsonrpc: '2.0',
                    id,
                    result: {
                        content: [
                            {
                                type: 'text',
                                text: content,
                            },
                        ],
                    },
                };
            } else {
                return {
                    jsonrpc: '2.0',
                    id,
                    error: {
                        code: -32000,
                        message: `LLM API error: ${result.status}`,
                        data: result.data,
                    },
                };
            }
        } catch (e) {
            return {
                jsonrpc: '2.0',
                id,
                error: { code: -32000, message: e.message },
            };
        }
    }

    return {
        jsonrpc: '2.0',
        id,
        error: { code: -32602, message: `Unknown tool: ${name}` },
    };
}

// ─── SSE 连接处理（MCP 流式传输） ───
function handleSSE(req, res) {
    res.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'Access-Control-Allow-Origin': '*',
    });

    // 发送端点信息
    res.write(`event: endpoint\ndata: /mcp/message\n\n`);

    // 保持心跳
    const keepAlive = setInterval(() => {
        res.write(':keepalive\n\n');
    }, 15000);

    req.on('close', () => {
        clearInterval(keepAlive);
    });
}

// ─── 创建 HTTP 服务器 ───
const server = http.createServer((req, res) => {
    // CORS
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-API-KEY');

    if (req.method === 'OPTIONS') {
        res.writeHead(204);
        res.end();
        return;
    }

    // API Key 校验
    if (MCP_KEY) {
        const authHeader = req.headers['x-api-key'] || req.headers['authorization'] || '';
        const token = authHeader.startsWith('Bearer ') ? authHeader.slice(7) : authHeader;
        if (token !== MCP_KEY) {
            res.writeHead(401, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: 'Unauthorized: Invalid or missing API KEY.' }));
            return;
        }
    }

    const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
    const path = url.pathname;

    // ── SSE 端点 ──
    if (path === '/sse' || path === '/mcp') {
        handleSSE(req, res);
        return;
    }

    // ── MCP 消息端点 ──
    if (path === '/mcp/message' || path === '/message') {
        if (req.method !== 'POST') {
            res.writeHead(405);
            res.end('Method Not Allowed');
            return;
        }

        let body = '';
        req.on('data', (chunk) => body += chunk);
        req.on('end', async () => {
            try {
                const json = JSON.parse(body);
                const response = handleMCPRequest(json);

                // 如果是 tools/call，需要 await
                if (json.method === 'tools/call') {
                    const asyncResponse = await handleToolCall(json.id, json.params);
                    res.writeHead(200, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify(asyncResponse));
                } else {
                    res.writeHead(200, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify(response));
                }
            } catch (e) {
                res.writeHead(400, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({
                    jsonrpc: '2.0',
                    id: null,
                    error: { code: -32700, message: 'Parse error: ' + e.message },
                }));
            }
        });
        return;
    }

    // ── 健康检查 ──
    if (path === '/health' || path === '/') {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
            status: 'ok',
            server: 'llama-mcp-server',
            version: '1.0.0',
            endpoint: LLAMA_ENDPOINT,
            port: PORT,
        }));
        return;
    }

    // ── 404 ──
    res.writeHead(404);
    res.end('Not Found');
});

// ─── 启动 ───
server.listen(PORT, '0.0.0.0', () => {
    console.log(`MCP Server 启动成功`);
    console.log(`  端口: ${PORT}`);
    console.log(`  后端: ${LLAMA_ENDPOINT}`);
    if (MCP_KEY) console.log(`  Key: ${MCP_KEY}`);
    console.log(`  SSE:  http://127.0.0.1:${PORT}/sse`);
    console.log(`  MCP:  http://127.0.0.1:${PORT}/mcp/message`);
    
    // 启动时获取模型列表
    fetchModels();
});
