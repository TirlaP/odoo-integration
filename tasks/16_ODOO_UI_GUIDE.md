# 16) Odoo UI guide (the screens you’re seeing)

This is a “what is this page for?” cheat sheet for the standard Odoo apps you opened.

## 16.1 Products: the tabs you showed

On a product (Goods / storable), these tabs are standard Odoo:
- **General Information**: name, SKU (Internal Reference), barcode, sales price, taxes, product category
- **Attributes & Variants**: only used if you model sizes/colors as Odoo variants (optional)
- **Sales**: upsell notes, sales description, optional products
- **Purchase**: vendor pricelists (who you buy from, at what price, lead time)
- **Inventory**: routes, weight/volume, customer lead time, logistics notes
- **Product Data**: depends on installed apps; often accounting/extra fields

In this repo you also have TecDoc-related tabs/pages:
- **Compatible Cars**: should show compatibility via TecDoc Fast DB (vehicles)
- **Equivalents**: should show OEM + cross references via TecDoc Fast DB

What you normally MUST set to have a working ERP:
- Product Type = **Goods**
- Track Inventory = **enabled**
- Internal Reference = your SKU (often TecDoc article number)
- Sales price + taxes
- Vendors in **Purchase** tab (at least the main one)

## 16.2 Purchase app (`/odoo/purchase`)

Core objects:
- **RFQ** (Request for Quotation): draft purchase order to a vendor
- **Purchase Order**: confirmed order to a vendor
- **Receipts**: incoming shipments; validation is what increases stock
- **Vendor Bills**: supplier invoices (Accounting)

Typical flow:
1) RFQ → confirm → Purchase Order
2) Receive goods (Receipt) → validate
3) Create Vendor Bill → post → pay

## 16.3 Inventory app (`/odoo/inventory`)

Core objects:
- **Receipts** (incoming): stock increases when you validate
- **Delivery Orders** (outgoing): stock decreases when you validate
- **Internal Transfers**: move stock between locations

Important concept:
- “Demand” = planned quantity
- “Done” = physically processed quantity

## 16.4 Invoicing / Accounting (`/odoo/customer-invoices`)

Customer invoices:
- residual amount is what drives “customer balance” if you use accounting-based balance

Vendor bills:
- drive purchase costs and payables

## 16.5 What you *do not* need to configure immediately

You can postpone until core flows work:
- multi-warehouse / bin locations
- landed costs
- advanced pricelists
- portal
- supplier API automation

