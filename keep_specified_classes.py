import pandas as pd

df = pd.read_csv("train_wkt_v4FULL.csv")

new_df = []
for _, row in df.iterrows():
    new_df
    if row["ClassType"] == 6:
        new_df.append({"ImageId": row["ImageId"],
                       "ClassType": 1,
                       "MultipolygonWKT": row["MultipolygonWKT"]})
    elif row["ClassType"] == 7:
        new_df.append({"ImageId": row["ImageId"],
                       "ClassType": 2,
                       "MultipolygonWKT": row["MultipolygonWKT"]})
    # else:
    #     new_df.append({"ImageId": row["ImageId"],
    #                    "ClassType": row["ClassType"],
    #                    "MultipolygonWKT": "MULTIPOLYGON EMPTY"})

pd.DataFrame(new_df)[["ImageId", "ClassType", "MultipolygonWKT"]].to_csv("train_wkt_v4.csv",
                                                                        index=False,
                                                                        header=True)