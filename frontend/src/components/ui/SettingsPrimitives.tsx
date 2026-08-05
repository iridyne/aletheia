import {
	cloneElement,
	useId,
	type AriaAttributes,
	type HTMLAttributes,
	type InputHTMLAttributes,
	type ReactElement,
	type ReactNode,
} from "react";

type FieldControlProps = {
	id?: string;
	"aria-describedby"?: string;
	"aria-invalid"?: AriaAttributes["aria-invalid"];
};

export interface FieldProps {
	label: ReactNode;
	description?: ReactNode;
	error?: ReactNode;
	controlId?: string;
	descriptionId?: string;
	className?: string;
	children: ReactElement<FieldControlProps>;
}

export function Field({
	label,
	description,
	error,
	controlId: providedControlId,
	descriptionId: providedDescriptionId,
	className = "",
	children,
}: FieldProps) {
	const generatedId = useId();
	const controlId = providedControlId ?? children.props.id ?? `settings-control-${generatedId}`;
	const descriptionId = description
		? (providedDescriptionId ?? `${controlId}-description`)
		: undefined;
	const errorId = error ? `${controlId}-error` : undefined;
	const describedBy = [
		children.props["aria-describedby"],
		descriptionId,
		errorId,
	]
		.filter(Boolean)
		.join(" ") || undefined;

	return (
		<div className={`settings-form-field${className ? ` ${className}` : ""}`}>
			<label className="settings-form-label" htmlFor={controlId}>
				{label}
			</label>
			{cloneElement(children, {
				id: controlId,
				"aria-describedby": describedBy,
				"aria-invalid": error ? true : children.props["aria-invalid"],
			})}
			{description && (
				<p className="settings-form-description" id={descriptionId}>
					{description}
				</p>
			)}
			{error && (
				<p className="settings-form-error" id={errorId} role="alert">
					{error}
				</p>
			)}
		</div>
	);
}

export function ConfigSection({
	id,
	title,
	description,
	children,
}: {
	id?: string;
	title: ReactNode;
	description?: ReactNode;
	children: ReactNode;
}) {
	return (
		<section className="settings-config-section" id={id}>
			<div className="settings-config-copy">
				<h3 className="settings-config-title">{title}</h3>
				{description && (
					<p className="settings-config-description">{description}</p>
				)}
			</div>
			<div className="settings-config-content">{children}</div>
		</section>
	);
}

export function SettingsScrollArea({
	children,
	className = "",
	...props
}: HTMLAttributes<HTMLDivElement>) {
	return (
		<div
			{...props}
			className={`settings-scroll-area${className ? ` ${className}` : ""}`}
			data-settings-scroll-area
		>
			{children}
		</div>
	);
}

export interface TextFieldConfig {
	key: string;
	label: string;
	description?: string;
	type?: "text" | "password" | "email" | "url" | "tel";
	placeholder?: string;
	autoComplete?: string;
	inputMode?: InputHTMLAttributes<HTMLInputElement>["inputMode"];
	disabled?: boolean;
	readOnly?: boolean;
	fullWidth?: boolean;
}

export function TextFieldGrid({
	fields,
	values,
	set,
	errors,
}: {
	fields: readonly TextFieldConfig[];
	values: Record<string, unknown>;
	set: (key: string, value: string) => void;
	errors?: Record<string, string>;
}) {
	return (
		<div className="settings-form-grid">
			{fields.map((field) => (
				<Field
					key={field.key}
					label={field.label}
					description={field.description}
					error={errors?.[field.key]}
					className={field.fullWidth ? "settings-form-field-full" : undefined}
				>
					<input
						type={field.type ?? "text"}
						value={String(values[field.key] ?? "")}
						placeholder={field.placeholder}
						autoComplete={field.autoComplete}
						inputMode={field.inputMode}
						disabled={field.disabled}
						readOnly={field.readOnly}
						onChange={(event) => set(field.key, event.target.value)}
					/>
				</Field>
			))}
		</div>
	);
}

export interface SelectOption {
	value: string;
	label: string;
	disabled?: boolean;
}

export interface SelectFieldConfig {
	key: string;
	label: string;
	description?: string;
	options: readonly SelectOption[];
	disabled?: boolean;
	fullWidth?: boolean;
}

export function SelectFieldGrid({
	fields,
	values,
	set,
	errors,
}: {
	fields: readonly SelectFieldConfig[];
	values: Record<string, unknown>;
	set: (key: string, value: string) => void;
	errors?: Record<string, string>;
}) {
	return (
		<div className="settings-form-grid">
			{fields.map((field) => {
				const value = String(values[field.key] ?? "");
				const hasKnownValue = field.options.some((option) => option.value === value);
				return (
					<Field
						key={field.key}
						label={field.label}
						description={field.description}
						error={errors?.[field.key]}
						className={field.fullWidth ? "settings-form-field-full" : undefined}
					>
						<select
							value={value}
							disabled={field.disabled}
							onChange={(event) => set(field.key, event.target.value)}
						>
							{!hasKnownValue && (
								<option value={value}>{value || "Not selected"}</option>
							)}
							{field.options.map((option) => (
								<option key={option.value} value={option.value} disabled={option.disabled}>
									{option.label}
								</option>
							))}
						</select>
					</Field>
				);
			})}
		</div>
	);
}
