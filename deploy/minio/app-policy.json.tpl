{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "WorkingBucketMetadata",
      "Effect": "Allow",
      "Action": [
        "s3:GetBucketLocation",
        "s3:ListBucket",
        "s3:ListBucketMultipartUploads"
      ],
      "Resource": "arn:aws:s3:::__WORKING_BUCKET__"
    },
    {
      "Sid": "WorkingObjectsMutable",
      "Effect": "Allow",
      "Action": [
        "s3:AbortMultipartUpload",
        "s3:DeleteObject",
        "s3:GetObject",
        "s3:ListMultipartUploadParts",
        "s3:PutObject"
      ],
      "Resource": "arn:aws:s3:::__WORKING_BUCKET__/*"
    },
    {
      "Sid": "OriginalsBucketMetadata",
      "Effect": "Allow",
      "Action": [
        "s3:GetBucketLocation",
        "s3:GetObjectLockConfiguration",
        "s3:ListBucket"
      ],
      "Resource": "arn:aws:s3:::__ORIGINALS_BUCKET__"
    },
    {
      "Sid": "OriginalsAppendAndVerifyOnly",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:GetObjectLegalHold",
        "s3:GetObjectRetention",
        "s3:PutObject",
        "s3:PutObjectLegalHold",
        "s3:PutObjectRetention"
      ],
      "Resource": "arn:aws:s3:::__ORIGINALS_BUCKET__/*"
    }
  ]
}
