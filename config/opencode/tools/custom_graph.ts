// The tool behind /custom-graph: runs the owner's question through the coded graph
// (scripts/harness/ask.py) on the session's project and returns the checked answer. A command may
// name another flow with <mode>NAME</mode> before the question (README, "Your own graphs").
// install-opencode.sh links this file into the window's opencode config folder. The graph's own
// headless runs use private opencode homes without it, so a node can never start another graph,
// and the settings hide it from every agent except the hidden graph agent.
// opencode 1.19.3 drops a custom tool's progress updates, so progress goes to
// <run folder>/progress.log, linked as ~/agent-data/ask/latest, and the run folder is linked as
// ~/agent-data/ask/sessions/<session id> for the window's graph panel (config/opencode/tui/).
// Esc stops the run.
// AGENT_ASK_DIR and AGENT_ASK_SCOPE replace the runs folder and the scope, for checks.
import { spawn, spawnSync } from "node:child_process"
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, realpathSync, renameSync, rmSync, symlinkSync, writeFileSync } from "node:fs"
import os from "node:os"
import path from "node:path"
import { fileURLToPath } from "node:url"

const repo = path.resolve(path.dirname(realpathSync(fileURLToPath(import.meta.url))), "../../..")
const OPEN = "<question>\n"
const CLOSE = "\n</question>"

function messageText(ctx: any) {
  const users = (ctx.messages ?? []).filter((m: any) => m.info?.role === "user")
  return (users.at(-1)?.parts ?? [])
    .filter((p: any) => p.type === "text" && !p.synthetic)
    .map((p: any) => p.text)
    .join("\n")
}

// The command puts the owner's text between the markers. The question is taken from the message
// itself, so a model that shortens or rewords it in the tool call changes nothing.
function questionOf(text: string, fallback: string) {
  const start = text.indexOf(OPEN)
  const end = text.lastIndexOf(CLOSE)
  if (start >= 0 && end >= start + OPEN.length) return text.slice(start + OPEN.length, end).trim()
  return (fallback ?? "").trim()
}

// Only the command's own text, before the question, may name the flow.
function modeOf(text: string) {
  const head = text.indexOf(OPEN) >= 0 ? text.slice(0, text.indexOf(OPEN)) : ""
  return head.match(/<mode>([A-Za-z0-9_-]+)<\/mode>/)?.[1]
}

// work or personal by the notes' rule (~/agent-data/hermes/scope.json); anything else is work.
function scopeOf(dir: string) {
  const r = spawnSync("python3", ["-I", path.join(repo, "scripts/opencode-memory/agent-notes.py"), "scope", dir], {
    encoding: "utf8",
  })
  return r.stdout?.trim() === "personal" ? "personal" : "work"
}

function relink(link: string, target: string) {
  try {
    mkdirSync(path.dirname(link), { recursive: true })
    const tmp = `${link}.${process.pid}`
    rmSync(tmp, { force: true })
    symlinkSync(target, tmp)
    renameSync(tmp, link)
  } catch {}
}

function runFolder(root: string, session: string) {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, "0")
  const base = path.join(root, `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`)
  let out = base
  for (let i = 2; existsSync(out); i++) out = `${base}-${i}`
  mkdirSync(out, { recursive: true })
  relink(path.join(root, "latest"), out)
  relink(path.join(root, "sessions", session), out)
  return out
}

export default {
  description:
    "Answers the owner's question with the coded question graph and returns the checked answer: the answer, " +
    "its claims and verdicts, and the report path. Takes 6 to 30 minutes. Call it once per /custom-graph message.",
  args: {
    question: { type: "string", description: "the owner's question, as given" },
  },
  async execute(args: { question?: string }, ctx: any) {
    const text = messageText(ctx)
    const q = questionOf(text, args.question ?? "")
    if (!q) return { title: "custom graph", output: "No question given: type it after the command.", metadata: {} }
    const mode = modeOf(text)
    const dir = ctx.directory
    const own = scopeOf(dir)
    const scope = process.env.AGENT_ASK_SCOPE || own
    const out = runFolder(process.env.AGENT_ASK_DIR || path.join(os.homedir(), "agent-data/ask"), ctx.sessionID)
    const progress = path.join(out, "progress.log")
    writeFileSync(progress, `custom graph from the window: scope ${scope} (the project's scope: ${own}), project ${dir}\n`)
    const log = openSync(progress, "a")
    const child = spawn(
      "python3",
      ["-I", path.join(repo, "scripts/harness/ask.py"), "--project", dir, "--scope", scope, "--out", out,
       ...(mode ? ["--mode", mode] : []), q],
      { cwd: dir, stdio: ["ignore", "pipe", log] },
    )
    closeSync(log)
    let stdout = ""
    child.stdout.setEncoding("utf8")
    child.stdout.on("data", (s: string) => (stdout += s))
    // ask.py stops its own runs on SIGTERM; SIGKILL only if it has not ended 30 s later.
    let force: ReturnType<typeof setTimeout> | undefined
    const stop = () => {
      child.kill("SIGTERM")
      force = setTimeout(() => child.kill("SIGKILL"), 30_000)
    }
    ctx.abort.addEventListener("abort", stop, { once: true })
    const code = await new Promise<number | null>((resolve) => {
      child.on("error", () => resolve(-1))
      child.on("close", (c: number | null) => resolve(c))
    })
    clearTimeout(force)
    ctx.abort.removeEventListener("abort", stop)
    if (ctx.abort.aborted) return { title: "custom graph", output: `Stopped. Run folder: ${out}`, metadata: { out } }
    if (code === 0) return { title: "custom graph", output: stdout.trim(), metadata: { out, report: path.join(out, "report.md") } }
    const tail = readFileSync(progress, "utf8").trim().split("\n").slice(-15).join("\n")
    return {
      title: "custom graph",
      output: `The custom graph failed (exit ${code}). Last lines of ${progress}:\n${tail}`,
      metadata: { out },
    }
  },
}
