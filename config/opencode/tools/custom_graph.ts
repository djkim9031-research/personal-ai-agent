// The tool behind /custom-graph and /custom-graph-build: runs the owner's request through the coded graph
// (scripts/harness/ask.py) on the session's project and returns its output. A command may name another
// flow with <mode>NAME</mode> before the question (README, "Your own graphs"). In build mode the run stops
// after the plan; "go" continues the session's waiting plan in its run folder, with the plan reused. A new
// build request gets the session's latest graph output as an earlier answer it may refer to.
// install-opencode.sh links this file into the window's opencode config folder. The graph's own
// headless runs use private opencode homes without it, so a node can never start another graph,
// and the settings hide it from every agent except the hidden graph agent.
// opencode 1.19.3 drops a custom tool's progress updates, so progress goes to
// <run folder>/progress.log, linked as ~/agent-data/ask/latest, and the run folder is linked as
// ~/agent-data/ask/sessions/<session id> for the window's graph panel (config/opencode/tui/).
// Esc stops the run.
// AGENT_ASK_DIR and AGENT_ASK_SCOPE replace the runs folder and the scope, for checks.
import { spawn, spawnSync } from "node:child_process"
import { appendFileSync, closeSync, existsSync, mkdirSync, openSync, readFileSync, realpathSync, renameSync, rmSync, symlinkSync, writeFileSync } from "node:fs"
import os from "node:os"
import path from "node:path"
import { fileURLToPath } from "node:url"

const repo = path.resolve(path.dirname(realpathSync(fileURLToPath(import.meta.url))), "../../..")
const OPEN = "<question>\n"
const CLOSE = "\n</question>"
const GO = /^go[.!]?$/i

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

// The latest output of a finished graph run earlier in the session (one with a report, so not a message
// such as "no plan is waiting"): what "the plan" in a build request most likely means.
function earlierOutput(ctx: any) {
  for (const m of [...(ctx.messages ?? [])].reverse()) {
    for (const p of [...(m.parts ?? [])].reverse()) {
      if (p.type === "tool" && p.tool === "custom_graph" && p.state?.status === "completed" && p.state?.metadata?.report
          && p.state?.output) return p.state.output
    }
  }
  return ""
}

// The session's last run when it is a build plan in this project waiting for go.
function waitingPlan(root: string, session: string, dir: string) {
  try {
    const run = realpathSync(path.join(root, "sessions", session))
    const g = JSON.parse(readFileSync(path.join(run, "graph.json"), "utf8"))
    if (g.flow === "build" && g.state === "planned" && g.project === dir) return { run, question: g.question as string }
  } catch {}
  return undefined
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
    "Runs the owner's request through the coded graph and returns its output: for a question, the checked answer, " +
    "its claims and verdicts, and the report path; for a change (build mode), the plan, or after go the change made " +
    "and checked. Takes 5 to 60 minutes. Call it once per /custom-graph or /custom-graph-build message.",
  args: {
    question: { type: "string", description: "the owner's request, as given" },
  },
  async execute(args: { question?: string }, ctx: any) {
    const text = messageText(ctx)
    let q = questionOf(text, args.question ?? "")
    const title = "custom graph"
    if (!q) return { title, output: "No question given: type it after the command.", metadata: {} }
    const mode = modeOf(text)
    const dir = ctx.directory
    const own = scopeOf(dir)
    const scope = process.env.AGENT_ASK_SCOPE || own
    const root = process.env.AGENT_ASK_DIR || path.join(os.homedir(), "agent-data/ask")
    let out: string
    const extra: string[] = []
    if (mode === "build" && GO.test(q)) {
      const w = waitingPlan(root, ctx.sessionID, dir)
      if (!w) {
        return { title, metadata: {},
                 output: "No plan is waiting in this session. Start with: /custom-graph-build <what to build or change>" }
      }
      out = w.run
      q = w.question
      extra.push("--resume", out, "--approve")
      if (existsSync(path.join(out, "earlier.md"))) extra.push("--context", path.join(out, "earlier.md"))
      relink(path.join(root, "latest"), out)
      appendFileSync(path.join(out, "progress.log"), `go from the window: the owner approved the plan; scope ${scope}\n`)
    } else {
      out = runFolder(root, ctx.sessionID)
      extra.push("--out", out)
      const earlier = mode === "build" ? earlierOutput(ctx) : ""
      if (earlier) {
        writeFileSync(path.join(out, "earlier.md"), earlier)
        extra.push("--context", path.join(out, "earlier.md"))
      }
      writeFileSync(path.join(out, "progress.log"),
                    `custom graph from the window: scope ${scope} (the project's scope: ${own}), project ${dir}\n`)
    }
    const progress = path.join(out, "progress.log")
    const log = openSync(progress, "a")
    const child = spawn(
      "python3",
      ["-I", path.join(repo, "scripts/harness/ask.py"), "--project", dir, "--scope", scope, ...extra,
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
    if (ctx.abort.aborted) return { title, output: `Stopped. Run folder: ${out}`, metadata: { out } }
    if (code === 0) return { title, output: stdout.trim(), metadata: { out, report: path.join(out, "report.md") } }
    const tail = readFileSync(progress, "utf8").trim().split("\n").slice(-15).join("\n")
    return {
      title,
      output: `The custom graph failed (exit ${code}). Last lines of ${progress}:\n${tail}`,
      metadata: { out },
    }
  },
}
