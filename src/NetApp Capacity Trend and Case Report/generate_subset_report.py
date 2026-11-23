'Shared candidate: use explicit local input, configuration and output paths.'
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return
    source_dir = sys.argv[1]
    output_dir = sys.argv[2]
    clusters = [c.strip() for c in sys.argv[3].split(',') if c.strip()]
    if not os.path.isdir(source_dir):
        print("source dir not found:", source_dir)
        return
    os.makedirs(output_dir, exist_ok=True)

    import generate_capacity_report as cap
    import generate_case_report as case
    import generate_device_report as dev
    cap.set_cluster_filter(clusters)
    case.set_cluster_filter(clusters)
    dev.set_cluster_filter(clusters)

    print("cluster scope:", ", ".join(clusters))
    print("source:", source_dir)
    print("output:", output_dir)
    print("\n=== Step 1: capacity trend ===")
    try:
        cap.generate_capacity_report(source_dir, output_dir)
    except Exception as e:
        print("capacity report error:", e)
    print("\n=== Step 2: case analysis ===")
    try:
        case.generate_case_report(source_dir, output_dir)
    except Exception as e:
        print("case report error:", e)
    print("\n=== Step 3: device inventory ===")
    try:
        dev.generate_device_report(source_dir, output_dir)
    except Exception as e:
        print("device report error:", e)
    print("\ndone. output:", output_dir)


if __name__ == "__main__":
    main()
