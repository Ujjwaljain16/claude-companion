import { decideApproval } from "@/app/actions";
import { ExecutionsTable } from "@/components/executions";
import { Badge, Button, Card, CardTitle, GatewayError, PageHeader } from "@/components/ui";
import { ago } from "@/lib/format";
import { gw } from "@/lib/gateway";
import type { Execution, Task } from "@/lib/types";

type Catalog = Record<string, { description: string; agents: string[] }>;

export default async function WorkflowsPage() {
  const [catalog, execs, approvals] = await Promise.all([
    gw<Catalog>("/api/workflows"),
    gw<Execution[]>("/api/executions?kind=workflow&limit=30"),
    gw<Task[]>("/api/tasks?kind=approval"),
  ]);
  if (!catalog.ok) return (<><PageHeader title="Agent workflows" /><GatewayError error={catalog.error} /></>);
  return (
    <>
      <PageHeader title="Agent workflows" description="LangGraph workflows run in the gateway with checkpoints, retries and human approval. Start them from Claude with execute_agent_workflow." />

      <Card className="mb-6 border-warn/40">
        <CardTitle hint="Only you can decide these">Waiting for your approval</CardTitle>
        {approvals.ok && approvals.data.length ? (
          <div className="space-y-4">
            {approvals.data.map((t) => (
              <div key={t.task_id} className="rounded-lg border border-line p-4">
                <div className="mb-2 flex items-center justify-between text-xs text-muted">
                  <span className="font-mono">{t.execution_id}</span><span>{ago(t.created_at)}</span>
                </div>
                <p className="text-sm font-medium text-fg">{String(t.payload.task)}</p>
                <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-fg/5 p-3 text-sm text-fg">{String(t.payload.draft)}</pre>
                <form action={decideApproval} className="mt-3 flex flex-wrap items-center gap-2">
                  <input type="hidden" name="task_id" value={t.task_id} />
                  <input name="comment" placeholder="Comment (optional)" className="min-w-64 flex-1 rounded-md border border-line bg-card px-3 py-1.5 text-sm text-fg" />
                  <Button type="submit" name="decision" value="approve">Approve</Button>
                  <Button type="submit" name="decision" value="reject" variant="danger">Reject</Button>
                </form>
              </div>
            ))}
          </div>
        ) : <p className="text-sm text-muted">Nothing is waiting for approval.</p>}
      </Card>

      <div className="mb-6 grid gap-4 lg:grid-cols-2">
        {Object.entries(catalog.data).map(([name, w]) => (
          <Card key={name}>
            <CardTitle>{name}</CardTitle>
            <p className="text-sm text-muted">{w.description}</p>
            <div className="mt-3 flex gap-1.5">{w.agents.map((a) => <Badge key={a} tone="info">{a}</Badge>)}</div>
          </Card>
        ))}
      </div>

      <Card>
        <CardTitle>Workflow executions</CardTitle>
        {execs.ok ? <ExecutionsTable rows={execs.data} empty="No workflow runs yet." /> : <p className="text-sm text-bad">{execs.error}</p>}
      </Card>
    </>
  );
}
