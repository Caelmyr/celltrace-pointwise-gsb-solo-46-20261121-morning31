# CellTrace EL

CellTrace EL is a local image bench for reviewing photovoltaic module electroluminescence captures. It keeps the grayscale originals, derived measurements and human review decisions together in one inspection.

## Start the bench

Run `docker compose up --build` in this directory and open [the local bench](http://127.0.0.1:18949). The container publishes on the loopback interface and stores inspections in a named volume. Stop with Ctrl+C or `docker compose stop`. To use another port, set `CELLTRACE_PORT`. A different Compose project name gives a separate volume and container namespace.

The native entry is `python app.py` under Python 3.11 with the versions in `requirements.txt`. It listens at `127.0.0.1:8000` by default. Use `HOST`, `PORT` and `CELLTRACE_DATA` to control the bind address, port and writable data directory. No external service or remote font is needed.

## Review a sample

1. Choose **Open training sample**, select an inspection and load it. Both samples are generated numerically. EL-24017 shows an otherwise complete panel with thin marks, uneven cell levels and an overexposed patch. The low-current acquisition for EL-24029 includes a cropped boundary. The inspection shows a synthetic-data label.
2. In **Image bench**, change captures or open **Quality**, **Cell levels**, **Dark regions**, **Crack traces**, **Current response**, or **Strings**. Use the tables to inspect a cell or download a CSV.
3. Click the image with **Probe** to see the plane pixel, source sensor position and calibrated level. **Region** measures a closed area drawn with clicks. **Transect** measures a strip between two image points and can export a chart and a CSV.
4. **Compare pair** displays both registered acquisitions with a common or per-image display scale. The side-by-side, divider and checkerboard views expose shared and image-specific pixel support.
5. **Panel plane** sets image size, physical scale, regular cell grid measurement insets and string rows or columns. **Corner placement** sets the four corners and orientation for the selected source. **Check grid** measures visible separator offsets against the declared cell grid.
6. In **Captures**, check the original histogram and download the native bytes. Assign a matching **Dark reference** and **Flat reference** to correct sensor variation. Reference **Banding** examines native row or column profiles. With two references of the same role and settings, **Compare reference captures** shows native drift and support changes.
7. Draw a region to save a **Finding**. The finding keeps the source role, source image digest, plane settings and calibration reference links. From the finding list, reopen the original sensor crop and rectified evidence or download an evidence packet.
8. Inspect the **Report** preview, assign a disposition and issue an immutable local evidence archive. An issued inspection can be opened as a working copy. The **Lot review** uses issued snapshots when available and shows image contact sheets and capture settings.
9. A working exchange ZIP includes the original bytes and working inspection state. Import checks the file inventory, image digests and settings before creating an editable inspection. A calibrated plane ZIP includes a floating-point TIFF, bit-coded support raster and the transform. Cell evidence ZIPs hold the selected crops and their measurements.

## Recipe brightness baselines

The **Baselines** view builds a reusable per-cell-position brightness reference for a recipe, so a new module can be checked position by position against the recipe's normal variation instead of its own average.

1. Open two or more **issued** high-current inspections that share one cell grid. Only confirmed-passing (issued) panels can be selected as reference samples.
2. Publish a baseline under a recipe name. The same recipe name always creates a new immutable version; older versions stay readable and can be retired or reactivated. Each version carries a content digest and a full event history.
3. Every cell position stores the panel-relative brightness shape (median and robust MAD), the absolute calibrated median, its observed range, the number of contributing panels and the module names behind it. Positions seen by fewer than three panels are marked **limited evidence**; positions with no supported reading are marked **no reference data**. Individual outlier samples are rejected per position with a robust z score, so one abnormal panel cannot move the limits. Partially covered reference panels simply leave some positions with fewer contributors.
4. In **Lot review**, the current inspection is compared with the active baseline for its lot (recipe). The check reports out-of-family positions, limited-evidence positions with their contributing panels, uniformly weak rows or columns, and a whole-panel absolute level change. Uniform dimming is evaluated against the stored reference medians, never normalized against the new panel's own mean, so a globally dark panel stays flagged.
5. Baseline detail and comparison views export PNG maps and CSV tables. The baseline record keeps the original acquisition settings used by every reference panel: drive current, exposure, bit depth, white level and whether dark and flat calibration references were applied.

## Input and measurement bounds

- Use single-page 8-bit or unsigned 16-bit grayscale PNG or TIFF files. The sensor level ceiling can be declared on upload, for example for 12-bit values stored in a 16-bit file. Supported originals have at least 32 px per side, at most 8 million pixels and a file size up to 22 MiB. Each inspection holds up to 18 originals. Working exchanges are capped at 64 MiB.
- Linearity of input sensor codes is assumed. DPI, monitor color transforms, physical panel size and drive current are separate from sample depth. Physical units require both panel dimensions. The app computes relative image evidence and supports human decisions.
- The plane is defined by an ordered convex quadrilateral and optional camera orientation per capture. Its upper-left origin uses pixel centers at half integers. Rectification samples bilinearly and flags pixels when the interpolation footprint cannot use the original or a selected calibration reference. The plane is capped at 1.5 million pixels. Individual resolution and layout limits are enforced in the forms and in the service.
- Native dark subtraction uses a matching dark exposure. Flat normalization uses a median reference and masks inadequately supported samples. Check the **Calibration** layer for skipped or applied references.
- Dark regions are connected areas below a cell-relative cutoff. Crack traces measure narrow valleys inside cell insets, with optional known busbar bands excluded. Both are image indications for review. Thresholds and optics affect the available results.
- Current response compares observation on the common support of the two declared planes, normalizes by exposure and summarizes eligible cells. Workbench display levels affect preview intensity. Original source data and calibrated readings are available separately.
- A production lot view displays up to the 12 most recently edited inspections. Groups reflect declared current, exposure, sensor range, grid and calibration availability.
- Configuration and findings use revision-checked SQLite writes. Original bytes and issued attachments use content addressing in the same data directory. Recipe brightness baselines are stored in the same SQLite database as immutable, content-digested versions with their own event log. Export an exchange package to transport a working inspection. Use the issued evidence package for the issued record.

`scripts/count_backend.py` can reproduce the project-owned Python package inventory. From this directory run `python3 scripts/count_backend.py --project-root . --source-root celltrace`. Renderer templates, development tooling and generated local data are outside that package count.
