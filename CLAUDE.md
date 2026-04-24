# bid-iq

Bid IQ is Liftnow's internal tool for ingesting government bid packages and developing complete bids — pricing, strategy, specification analysis, and submittal documentation. The goal is one-click bid completion, with user input only where judgement is required.

## Orientation for Claude

Read this before making changes:

- The product is **Liftnow** — lowercase `n`, one word. Never "LiftNow" or "LIFTNOW". This is a strict voice rule enforced throughout the codebase.
- Postgres (Neon serverless) is the single source of truth. The two tables that matter are `brands` and `knowledge_items`. Schema lives in `lib/db.ts` (and mirrored in `scripts/setup-db.mjs`).
- `data/catalog.db` is a SQLite **historical artifact** that has already been migrated into Postgres. The running app does not read it. Do not add code that queries it.
- Legacy JSON extractions in `kb_extracted/` and `kb_output/` are Mohawk-only and have been migrated into `knowledge_items`. They stay on disk temporarily; do not re-wire the old `/api/db-seed` loader (it was deleted).
- The previous `products` table is retired. All product data lives in `knowledge_items` under `category = 'product-specifications'`, with an optional `brand_id` FK to `brands`.
- Q&A goes through `/api/ask`, which queries `knowledge_items` and calls Claude with the Liftnow system prompt. Do not branch out a parallel Q&A route.
- Classification uses the 10-category vocabulary defined in `app/knowledge-base/page.tsx`: `product-specifications`, `competitive-intelligence`, `pricing-data`, `bid-history`, `installation-guides`, `manufacturer-info`, `service-procedures`, `compliance-certifications`, `customer-intelligence`, `general`.
- A new PDF ingestion pipeline that reads from `data/product_data/<brand>/…` is planned but **not yet built.** `bidiq/enrich.py` is retained as a historical Mohawk-only extractor; do not extend it.
- `.github/workflows/extract-lfs-pdfs.yml.disabled` is intentionally disabled (suffix-renamed). Do not re-enable.

See `README.md` for route, env-var, and local-run details.

---

## Bid intake playbook

Here are some notes for bid intake:
o	Intake
	Read the title and description – does this fit within the scope of what our company does or is capable of supplying on a very surface level look?
	Read and verify important dates – Bid Deadline, Q&A Deadline, Site Visits (if mandatory or not), etc. 
	Structural bid timelines that inform response speed? (When is bid due, does it need to be physically mailed?) 
	“Substitutions”, “Alternate”, “Equivalent”, “Equal” – guides the discussion
	Any other time-based barriers – do we need to register for a bid portal or something similar? Does this take time, is this a manual registration and acceptance process by the agency? 
	If time based - What forms need to be filled out in our bid for responsiveness (required forms, required licensing, insurance, bonds (bid bond vs. performance bond, cashier’s check) etc.). 
	Forms – are there any that can be done after bid evaluation (we want to compress time to bid, and want to minimize documentation requests until after we are more sure we are being awarded)
	Administrative Evaluation - Other administrative requirements – am I the one authorized to sign this for my company? Authorized to sign and secure proper administrative requirements above, if not they need to be routed. Is there project bonding? I need time to get that together as well. 
	Technical Evaluation – What is being asked for? If brand names and parts have been called out, it creates an easier blueprint and also tells you at least 1 product they are looking at. If they haven’t, we want to figure out how they created the specifications to find out which product they are looking at. Usually a Google Search on the product will bring up a correct product. 
	If the specs don’t exactly match something currently on the marketplace (Google Search), are they from an old iteration of a product, or “Frankenstein” specs? Deficiencies in specs that go uncalled can provide a defensive and strategic method to get a bid tossed or in our favor. Other times, deficiencies in specs work against you. 
	If it’s a product + service, what do they say about the service they want provided? If its service-only, what does it say? Are there certain services specifically included or excluded? Ambiguity can provide defense. 
	Historical Evaluation – What has worked in the past for me? What has worked in the past with this customer? Does this particular customer have a known way that they are going to grade either this proposal (spelled out as a grading criteria or rubric) or a known way that they have a way of grading ALL proposals (this may vary by exact department, purchaser, etc.) based on past proposals or experience .. e.g. how likely are they to accept cheaper alternates? How likely are they to accept their specified product? A local vs. national company? Etc. 
	Strategic Evaluation (incl. Competitive Evaluation)  
	The best outcome is always – you have the exact product they want with no substitutions (rare). This creates extreme competitive moat, and will now likely be solely about price, which can be high with lack of competitors. Lower effort bid, higher margin win. Competitors don’t eat margins. 
	The next best outcome is always – you have the exact product they want, with a well-written/accurate specification by the customer. This is the next best outcome because you can respond using the product they want, and then defend the exact product using the customer’s own specifications, calling out deficiencies in nonconforming products. The onus is on the nonconforming vendor to explain why. 
	The next best outcome is that you have a product that meets/exceeds the specifications that have been written in every way, although it is not the exact product being requested. You can defend your product and explain how/why it conforms to specifications. This then may become about a “non-product” evaluation discussion – either price, service, experience/references, or both. 
	The second worst outcome is that you do not have a product available that meets specifications being written (with a well-written specification geared toward a competitive product), and you must now explain how or why your product not meeting the specifications is either unimportant for the customer or acceptance of their original product is disadvantageous for them (whether pricewise or otherwise). 
	The worst outcome is that you do not have a product available that meets specifications, and they demand no substitutions. Your only recourse is pre-bid to attempt to discredit or disqualify a vendor, and convince the user that there isn’t a real or statutory reason that they have stated “no substitutions”. You may be able to scare them off of it to open the bid or specification. 
	Pricing and Margin Evaluation 
	Based on product/service being requested, asks of the bid, geography of the bid (google location – remote is better), visibility of the bid, etc. you must figure out who is likely to be bidding (who, how many bidders, how might those customers be pricing their own products and solutions). Use historical bid data where possible.
	Bonus would be to figure out a customer budget if one exists, and any customer urgency regarding funds 
	Small things like a lack of questions or expressed interest through a bid portal on a complicated bid, extending bid deadlines, difficult/badly covered geography, bad bid dates or not enough time from bid release to a bid close, a very difficult bid process all tell us there’s less competition, which would inform a larger margin. 
	If you can submit multiple bids (find out if allowed), will you be able to capture multiple of these strategies above at different pricing bands to “straddle” other competitive bids. For instance, you could have a fully conforming bid at $20,000, a nonconforming specification bid at $15,000 to try to capture multiple attitudes/profile of that customer without knowing who they are first – price or product first. 
	For the bid itself, how involved is it and what are the risks involved? These risks could be in the scope (what is said or is NOT said, and how those chips may fall upon a purchase order or contract – again, some agencies pay up and others will grip harder). 
	Higher risk or involvement, lower # of legitimate bidders or competitors = Highest margin 
	Lower risk or involvement, higher # of legitimate bidders or competitors = Lowest margin
	In terms of pricing lookup, you now want to develop all of your costs and take a guess at your competitor’s costs. If you think there will be no competitors, then skip this step. If a project is re-bid, find out why? 
	What are all of the costs that are going to go into this project? 
	Products, services, freight/delivery, setup/training, additional warranty, project/performance bonds (if required), permitting (if required), equipment rentals, small contingency (on larger projects), specialty licensing or wage requirements (if required).
	Usually low touch/small $ value projects, we’re looking at only product, service, freight/delivery, setup/training, perhaps equipment rentals. 
	Side note to process: Thinking about low-end centralized self-fulfillment – meeting cheap bids where they are online, and filling out them in a large way ourselves to capture cheap low touch little to no review sales. 
o	Pricing lookup
	Product guidance is going to come from manufacturer price sheets. It is important these sit somewhere, are easily searchable/findable, including accessories and options, etc.
	Freight guidance from manufacturer
	Setup/training/installation guidance from manufacturer
	Installation – understanding what it is and is not in the eyes of the manufacturer being bid, resources for manufacturers, etc. Ideally we’d like to develop what is called a “standard” for the words installation, removal, etc. in the eyes of the manufacturer to defend. Then we’d like to get pricing guidance on those things if not ordered from a manufacturer (third parties, how do we figure this out?)
	There is a general concept of arbitraging against those companies (product provider-service provider hybrids) who fill out bids (have administrative know-how, sometimes a dedicated person on staff, usually higher overhead) in favor of subcontracting or utilizing companies who are technically proficient in meeting the specifications, but do not have the overhead (we step in as their overhead) and would keep cost differences that ensue.
	After understanding general pricing rules for installations, we’d like to understand the add-ons being asked by a customer, and how those things are priced. Developing pricing guidance on hourly basis, parts basis, etc. for each industry is going to be important. 
o	Proposal writing
	Cover letter for a bid 
	Statement of Qualifications/Capabilities Statement 
	State Contract, Sourcewell Name Drop
	History in “X” State, County, Community, etc.
	Add into bid document that our Service Program through contracts can be laid on top off the bid that they are soliciting. 
	Specification deviations/reasons why our product works and is good.  

We should be able to figure out what products are being requested (named or not) from the bid. Performing spec searches will tell you this, and you will have to connect to the internet to find minimum acceptable products. From there, you will look to provide options for bids, with precedence being spec compliance and THEN price.
You should be able to auto attach spec sheets from manufacturer websites for those units. 

You should also be able to build in auto protest rules, auto follow up for the customer contacts attached to the bid to ensure we don't let anything get stale, go unresponded, or if the bid doesn't go our way we have a "stick" approach where we can file protests timely
