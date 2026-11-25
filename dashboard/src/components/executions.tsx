import Link from "next/link";
import { cancelExecution } from "@/app/actions";
import { ago, statusTone } from "@/lib/format";
import type { Execution } from "@/lib/types";
import { Badge, Button, Table, Td } from "./ui";

export function ExecutionsTable({ rows, empty }: { rows: Execution[]; empty?: string }) {
  return (
    <Table head={["Execution", "Kind", "Status", "Message", "Started by", "Updated", ""]} empty={empty}>
      {rows.map((e) => (
        <tr key={e.execution_id}>
          <Td>
            <Link className="font-mono text-xs text-accent hover:underline" href={`/traces?execution=${e.execution_id}`}>
              {e.execution_id}
            </Link>
          </Td>
          <Td>{e.kind}</Td>
          <Td>
            <Badge tone={statusTone(e.status)}>{e.status}</Badge>
          </Td>
          <Td className="max-w-md truncate text-muted">{e.error ?? e.message}</Td>
          <Td className="text-muted">{e.actor}</Td>
          <Td className="whitespace-nowrap text-muted">{ago(e.updated_at)}</Td>
          <Td>
            {!e.terminal ? (
              <form action={cancelExecution}>
                <input type="hidden" name="execution_id" value={e.execution_id} />
                <Button variant="danger" type="submit">Cancel</Button>
              </form>
            ) : null}
          </Td>
        </tr>
      ))}
    </Table>
  );
}
