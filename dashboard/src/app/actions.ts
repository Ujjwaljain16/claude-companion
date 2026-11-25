"use server";

import { revalidatePath } from "next/cache";
import { gw } from "@/lib/gateway";

export async function cancelExecution(formData: FormData) {
  const id = String(formData.get("execution_id") ?? "");
  if (!/^[a-f0-9]{16}$/.test(id)) return;
  await gw(`/api/executions/${id}/cancel`, { method: "POST" });
  revalidatePath("/", "layout");
}

export async function decideApproval(formData: FormData) {
  const id = String(formData.get("task_id") ?? "");
  if (!/^[a-f0-9]{16}$/.test(id)) return;
  const approved = formData.get("decision") === "approve";
  const comment = String(formData.get("comment") ?? "").slice(0, 1000);
  await gw(`/api/tasks/${id}/submit`, { method: "POST", body: { response: { approved, comment } } });
  revalidatePath("/workflows");
}
