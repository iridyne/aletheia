import type { ReactNode } from "react";
import { SettingsSection } from "../../components/ui";

export type SettingsFeatureTab =
	| "agent"
	| "execution"
	| "risk"
	| "health"
	| "runtime"
	| "signals"
	| "advanced";

export interface SettingsFeatureDescriptor {
	id: string;
	title: string;
	tab: SettingsFeatureTab;
	description: string;
	searchTerms: readonly string[];
}

export function SettingsFeatureSection({
	descriptor,
	error,
	pendingLabel,
	children,
}: {
	descriptor: SettingsFeatureDescriptor;
	error?: string | null;
	pendingLabel?: string | null;
	children: ReactNode;
}) {
	return (
		<SettingsSection
			id={descriptor.id}
			title={descriptor.title}
			description={descriptor.description}
		>
			{error && (
				<div className="save-err-banner" role="alert">
					{error}
				</div>
			)}
			{pendingLabel && (
				<div className="settings-feature-pending" role="status">
					{pendingLabel}
				</div>
			)}
			{children}
		</SettingsSection>
	);
}
