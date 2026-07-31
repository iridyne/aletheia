export function GuardAuthorityNotice({ legacyAutoPromoteEnabled = false }: { legacyAutoPromoteEnabled?: boolean }) {
  return (
    <div className="hint mb-2">
      <p>Owner-authored guards can enforce their configured effect. Learned guards remain observe-only by default; loss counts alone never grant blocking or score-penalty authority.</p>
      <p>Learned effects require an exact prospective experiment treatment assignment. Candidate expiry and deprecation continue automatically.</p>
      {legacyAutoPromoteEnabled && (
        <p className="text-warning">Legacy auto-promote is saved in config, but it cannot promote or unshadow learned guard candidates.</p>
      )}
    </div>
  )
}
