// The window's view of a /custom-graph run: the steps of its flow in the session's sidebar, each with
// its state, read every second from the run folder that the tool links as
// <runs folder>/sessions/<session id> (graph.json from graph.py, summary.json from ask.py).
// config/opencode/tui.json lists this file and the launcher passes that file to opencode; headless
// runs never load it. A session without a run shows nothing.
import type { TuiPlugin, TuiPluginApi } from "@opencode-ai/plugin/tui"
import { For, Show, createMemo, createSignal, onCleanup } from "solid-js"
import { readFileSync, readlinkSync, statSync } from "node:fs"
import os from "node:os"
import path from "node:path"

const ROOT = process.env.AGENT_ASK_DIR || path.join(os.homedir(), "agent-data/ask")
const LABEL: Record<string, string> = { q: "facts", m: "mechanisms" }
const MARK: Record<string, string> = {
  done: "✓",
  reused: "✓",
  running: "●",
  waiting: "○",
  skipped: "-",
  capped: "✗",
  timeout: "✗",
  failed: "✗",
}
const OK = new Set(["done", "reused"])

function json(file: string) {
  try {
    return JSON.parse(readFileSync(file, "utf8"))
  } catch {
    return undefined
  }
}

function mtime(file: string) {
  try {
    return statSync(file).mtimeMs
  } catch {
    return 0
  }
}

function clock(ms: number) {
  const s = Math.max(0, Math.round(ms / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`
}

// A stage's state from its runs: running while one runs, waiting until one starts, then done or not.
function stageState(states: string[]) {
  if (!states.length || states.every((x) => x === "waiting")) return "waiting"
  if (states.includes("running") || states.includes("waiting")) return "running"
  if (states.every((x) => x === "skipped")) return "skipped"
  return states.every((x) => OK.has(x)) ? "done" : "failed"
}

function View(props: { api: TuiPluginApi; session_id: string }) {
  const theme = () => props.api.theme.current
  const [run, setRun] = createSignal<{ dir: string; graph: any; summary: any } | undefined>()
  const [now, setNow] = createSignal(Date.now())
  let seen = ""
  const tick = () => {
    setNow(Date.now())
    let dir = ""
    try {
      dir = readlinkSync(path.join(ROOT, "sessions", props.session_id))
    } catch {}
    const key = dir && `${dir} ${mtime(path.join(dir, "graph.json"))} ${mtime(path.join(dir, "summary.json"))}`
    if (key === seen) return
    const graph = dir ? json(path.join(dir, "graph.json")) : undefined
    if (dir && !graph) return // not written yet: next second
    seen = key
    setRun(dir ? { dir, graph, summary: json(path.join(dir, "summary.json")) } : undefined)
  }
  tick()
  const timer = setInterval(tick, 1000)
  onCleanup(() => clearInterval(timer))

  const color = (state: string) =>
    OK.has(state) ? theme().success : state === "running" ? theme().warning : state === "waiting" || state === "skipped"
      ? theme().textMuted
      : theme().error

  const head = createMemo(() => {
    const g = run()?.graph
    if (!g) return ""
    const start = Date.parse(g.started)
    const end = g.ended ? Date.parse(g.ended) : now()
    return `${g.state === "running" ? "running" : g.state} ${clock(end - start)}`
  })

  const stages = createMemo(() => {
    const g = run()?.graph
    if (!g) return []
    const nodes = Object.entries(g.nodes ?? {}) as [string, any][]
    return (g.stages ?? [])
      .filter((s: any) => s.kind === "model")
      .map((s: any) => {
        const runs = nodes.filter(([, n]) => n.node === s.id).sort(([a], [b]) => a.localeCompare(b))
        const states = runs.map(([, n]) => n.status ?? "waiting")
        const state = stageState(states)
        const finished = runs.filter(([, n]) => !["waiting", "running"].includes(n.status)).length
        const single = runs.length === 1 && !s.fanout ? runs[0][1] : undefined
        const right = s.fanout
          ? runs.length
            ? `${finished}/${runs.length}`
            : ""
          : single && OK.has(single.status)
            ? `${Math.round(single.seconds ?? 0)}s`
            : state === "skipped"
              ? "skipped"
              : ""
        const live = runs
          .filter(([, n]) => n.status === "running")
          .map(([id, n]) => `${id} ${clock(now() - Date.parse(n.start))}, ${n.tools ?? 0} tool calls`)
        return { label: LABEL[s.id] ?? s.id, state, right, cells: s.fanout ? runs : [], live }
      })
  })

  const foot = createMemo(() => {
    const r = run()
    if (!r?.graph || r.graph.state !== "done") return ""
    const v = r.summary?.verdicts ?? {}
    const counts = Object.entries(v).map(([k, n]) => `${n} ${k}`).join(", ")
    return `${counts ? `claims: ${counts}\n` : ""}run ${path.basename(r.dir)}`
  })

  return (
    <Show when={run()?.graph}>
      <box>
        <box flexDirection="row" gap={1} justifyContent="space-between">
          <text fg={theme().text}>
            <b>Custom graph</b>
          </text>
          <text fg={theme().textMuted}>{head()}</text>
        </box>
        <For each={stages()}>
          {(s) => (
            <box>
              <box flexDirection="row" gap={1} justifyContent="space-between">
                <text fg={theme().text}>
                  <span style={{ fg: color(s.state) }}>{MARK[s.state] ?? "○"}</span> {s.label}
                </text>
                <text fg={theme().textMuted}>{s.right}</text>
              </box>
              <Show when={s.cells.length}>
                <text>
                  {"  "}
                  <For each={s.cells}>
                    {([, n]: [string, any]) => <span style={{ fg: color(n.status) }}>{MARK[n.status] ?? "○"}</span>}
                  </For>
                </text>
              </Show>
              <For each={s.live}>{(line) => <text fg={theme().textMuted}>{"    " + line}</text>}</For>
            </box>
          )}
        </For>
        <Show when={foot()}>
          <text fg={theme().textMuted}>{foot()}</text>
        </Show>
      </box>
    </Show>
  )
}

const tui: TuiPlugin = async (api) => {
  api.slots.register({
    order: 50,
    slots: {
      sidebar_content(_ctx, props) {
        return <View api={api} session_id={props.session_id} />
      },
    },
  })
}

export default { id: "agent.graph-view", tui }
