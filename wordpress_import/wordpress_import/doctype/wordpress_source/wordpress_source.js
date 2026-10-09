// SPDX-License-Identifier: AGPL-3.0-or-later
frappe.ui.form.on("WordPress Source", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Import Now"), () => {
			frappe.call({
				method: "wordpress_import.api.import_now",
				args: { source: frm.doc.name },
				freeze: true,
				freeze_message: __("Importing from WordPress…"),
				callback: () => frm.reload_doc(),
			});
		});
	},
});
