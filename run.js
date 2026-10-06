/**
 * 前后端一键启动脚本
 *
 * 用法:
 *   node run.js            - 同时启动前后端
 *   node run.js backend    - 只启动后端
 *   node run.js frontend   - 只启动前端
 */

const { spawn } = require("child_process");
const path = require("path");
const os = require("os");
const readline = require("readline");

// ANSI 颜色代码
const colors = {
    reset: "\x1b[0m",
    bright: "\x1b[1m",
    red: "\x1b[31m",
    green: "\x1b[32m",
    yellow: "\x1b[33m",
    blue: "\x1b[34m",
    magenta: "\x1b[35m",
    cyan: "\x1b[36m",
};

// 日志输出函数
function log(prefix, message, color = colors.reset) {
    const timestamp = new Date().toLocaleTimeString("zh-CN", { hour12: false });
    console.log(`${color}[${timestamp}] [${prefix}]${colors.reset} ${message}`);
}

const BACKEND_DIR = __dirname;

// Windows 上优先使用 python，避免从 app 目录启动导致相对包导入失败。
const PYTHON_CMD = os.platform() === "win32" ? "python" : "python3";

// 启动后端服务
function startBackend() {
    return new Promise((resolve, reject) => {
        // uvicorn app.main:app --reload --port 8000 --app-dir apps/api
        const backend = spawn(
            PYTHON_CMD,
            ["-m", "uvicorn", "app.main:app", "--reload", "--reload-dir", "apps/api/app", "--port", "8000", "--app-dir", "apps/api"],
            {
                cwd: BACKEND_DIR,
                shell: true,
                stdio: "pipe",
            },
        );

        // 监听输出
        backend.stdout.on("data", (data) => {
            const output = data.toString().trim();
            // 检测启动成功信号
            if (
                output.includes("Uvicorn running") ||
                output.includes("Application startup complete")
            ) {
                resolve(backend);
            }
        });

        backend.stderr.on("data", (data) => {
            const output = data.toString().trim();
            if (output) {
                // 某些正常日志也会输出到 stderr
                if (output.includes("INFO") || output.includes("WARNING")) {
                    log("后端", output, colors.yellow);
                } else {
                    log("后端", output, colors.red);
                }
            }
        });

        backend.on("error", (error) => {
            log("后端", `启动失败: ${error.message}`, colors.red);
            reject(error);
        });

        backend.on("close", (code) => {
            if (code !== 0 && code !== null) {
                log("后端", `进程退出，代码: ${code}`, colors.red);
            }
        });

        // 超时处理
        setTimeout(() => {
            log("后端", "启动完成（可能需要几秒钟加载）", colors.green);
            resolve(backend);
        }, 3000);
    });
}

// 启动前端服务
function startFrontend() {
    return new Promise((resolve, reject) => {
        const frontendDir = path.join(__dirname, "apps/desktop");

        // 检测包管理器
        let packageManager = "pnpm";

        // pnpm dev
        const frontend = spawn(packageManager, ["dev"], {
            cwd: frontendDir,
            shell: true,
            stdio: "pipe",
        });

        // 监听输出
        frontend.stdout.on("data", (data) => {
            const output = data.toString().trim();
            // 检测启动成功信号
            if (output.includes("ready in") || output.includes("Local:")) {
                resolve(frontend);
            }
        });

        frontend.stderr.on("data", (data) => {
            const output = data.toString().trim();
            if (output) {
                // Vite 的某些日志也会输出到 stderr
                if (output.includes("error") || output.includes("Error")) {
                    log("前端", output, colors.red);
                } else {
                    log("前端", output, colors.yellow);
                }
            }
        });

        frontend.on("error", (error) => {
            log("前端", `启动失败: ${error.message}`, colors.red);
            reject(error);
        });

        frontend.on("close", (code) => {
            if (code !== 0 && code !== null) {
                log("前端", `进程退出，代码: ${code}`, colors.red);
            }
        });

        // 超时处理
        setTimeout(() => {
            log("前端", "启动完成", colors.green);
            resolve(frontend);
        }, 5000);
    });
}

// 主函数
async function main() {
    const args = process.argv.slice(2);
    const flags = args.filter((arg) => arg.startsWith("--"));
    const mode = args.find((arg) => !arg.startsWith("--")) || "all";
    const noMigrate = flags.includes("--no-migrate");

    const processes = [];

    try {
        if (!["all", "backend", "frontend"].includes(mode)) {
            log(
                "系统",
                `未知模式 "${mode}"，可用：all（默认）/ backend / frontend，选项 --no-migrate`,
                colors.red,
            );
            process.exit(1);
        }

        // 启动后端
        if (mode === "all" || mode === "backend") {
            const backend = await startBackend();
            processes.push(backend);
            log("系统", "后端服务已启动: http://localhost:8000", colors.green);
        }

        // 启动前端
        if (mode === "all" || mode === "frontend") {
            // 如果是 all 模式，等待后端先启动
            if (mode === "all") {
                log("系统", "等待后端完全启动...", colors.yellow);
                await new Promise((resolve) => setTimeout(resolve, 2000));
            }

            const frontend = await startFrontend();
            processes.push(frontend);
            log("系统", "前端服务已启动: http://localhost:5173", colors.green);
        }

        log("系统", "所有服务已启动完成！", colors.bright + colors.green);
        console.log(
            colors.bright + colors.green + "=".repeat(60) + colors.reset + "\n",
        );
    } catch (error) {
        log("系统", `启动失败: ${error.message}`, colors.red);
        // 清理已启动的进程
        processes.forEach((proc) => {
            if (proc && !proc.killed) {
                proc.kill();
            }
        });
        process.exit(1);
    }

    // 处理退出信号
    const cleanup = () => {
        console.log("\n");
        log("系统", "正在停止所有服务...", colors.yellow);

        processes.forEach((proc, index) => {
            if (proc && !proc.killed) {
                const serviceName = index === 0 ? "后端" : "前端";
                log(serviceName, "正在停止...", colors.yellow);
                proc.kill("SIGTERM");

                // 强制终止超时处理
                setTimeout(() => {
                    if (!proc.killed) {
                        proc.kill("SIGKILL");
                    }
                }, 3000);
            }
        });

        setTimeout(() => {
            log("系统", "所有服务已停止", colors.green);
            process.exit(0);
        }, 1000);
    };

    // 监听退出信号
    process.on("SIGINT", cleanup); // Ctrl+C
    process.on("SIGTERM", cleanup); // kill 命令

    // Windows 特殊处理
    if (os.platform() === "win32") {
        readline
            .createInterface({
                input: process.stdin,
                output: process.stdout,
            })
            .on("SIGINT", cleanup);
    }
}

// 错误处理
process.on("uncaughtException", (error) => {
    log("系统", `未捕获的异常: ${error.message}`, colors.red);
    process.exit(1);
});

process.on("unhandledRejection", (reason, promise) => {
    log("系统", `未处理的 Promise 拒绝: ${reason}`, colors.red);
    process.exit(1);
});

// 运行
if (require.main === module) {
    main().catch((error) => {
        log("系统", `运行失败: ${error.message}`, colors.red);
        process.exit(1);
    });
}

module.exports = {
    startBackend,
    startFrontend,
};
