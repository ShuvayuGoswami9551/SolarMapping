import s3fs


def last_part(path):
    return path.rstrip("/").split("/")[-1]


fs = s3fs.S3FileSystem(anon=True)

for bucket in ["noaa-goes16", "noaa-goes19"]:
    for product in ["ABI-L2-DSRC", "ABI-L2-DSRF", "ABI-L2-DSRM"]:
        base = f"{bucket}/{product}"
        print(f"\n{base}")

        try:
            years = sorted(last_part(p) for p in fs.ls(base))
        except Exception as error:
            print(f"  Could not list: {error}")
            continue

        print(f"  Years: {years}")

        for year in years:
            try:
                days = sorted(
                    last_part(p) for p in fs.ls(f"{base}/{year}")
                )
            except Exception as error:
                print(f"  {year}: could not list ({error})")
                continue

            if not days:
                print(f"  {year}: no day folders")
                continue

            print(
                f"  {year}: {len(days)} day folders, "
                f"first {days[0]}, last {days[-1]}"
            )

        if years:
            year = years[-1]
            days = sorted(last_part(p) for p in fs.ls(f"{base}/{year}"))

            if days:
                day = days[-1]
                hours = sorted(
                    last_part(p) for p in fs.ls(f"{base}/{year}/{day}")
                )

                if hours:
                    hour = hours[0]
                    files = fs.ls(f"{base}/{year}/{day}/{hour}")

                    print(
                        f"  Sample folder {year}/{day}/{hour}: "
                        f"{len(files)} files"
                    )

                    for path in files[:3]:
                        print(f"    {last_part(path)}")