const fs = require("node:fs");
const path = require("node:path");

function createConfig(userDataPath) {
  const file = path.join(userDataPath, "forge-desktop.json");
  const defaults = { workspaceRoot: "", apiPort: 0 };

  function read() {
    try {
      return { ...defaults, ...JSON.parse(fs.readFileSync(file, "utf8")) };
    } catch {
      return { ...defaults };
    }
  }

  function write(next) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(next, null, 2) + "\n", "utf8");
    return next;
  }

  return {
    file,
    read,
    update(patch) {
      return write({ ...read(), ...patch });
    },
  };
}

module.exports = { createConfig };
