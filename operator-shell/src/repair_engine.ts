export interface RepairDecision {
  shouldExecute: boolean
  rationale: string
}

export class RepairEngine {
  evaluate(): RepairDecision | null {
    return null
  }
}
