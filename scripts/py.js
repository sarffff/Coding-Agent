const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");

const root = path.resolve(__dirname, "..");
const venvPython = os.platform() === "win32"
  ? path.join(root, ".venv", "Scripts", "python.exe")
  : path.join(root, ".venv", "bin", "python");

const pythonCmd = fs.existsSync(venvPython)
  ? venvPython
  : (os.platform() === "win32" ? "python" : "python3");

const child = spawn(pythonCmd, process.argv.slice(2), {
  cwd: root,
  stdio: "inherit",
  shell: false,
});

child.on("close", (code) => {
  process.exit(code ?? 0);
});
