import boto3
import datetime
import json

REGION = 'us-east-1'
THRESHOLD = 15.0
SNS_TOPIC_ARN = 'arn:aws:sns:us-east-1:887137766138:ec2-idle-alerts-fresh'
S3_BUCKET = 'ec2-idle-reports-haridas'

ec2 = boto3.client('ec2', region_name=REGION)
cloudwatch = boto3.client('cloudwatch', region_name=REGION)
sns = boto3.client('sns', region_name=REGION)
s3 = boto3.client('s3', region_name=REGION)

def lambda_handler(event, context):
    now = datetime.datetime.utcnow()
    start_time = now - datetime.timedelta(hours=1)
    end_time = now

    instances = ec2.describe_instances(
        Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
    )

    idle_instances = []
    report = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "region": REGION,
        "instances_checked": 0,
        "idle_instances": [],
        "stopped_instances": [],
        "estimated_cost": "Rs. 0.0"
    }

    for reservation in instances['Reservations']:
        for instance in reservation['Instances']:
            instance_id = instance['InstanceId']
            report['instances_checked'] += 1

            metrics = cloudwatch.get_metric_statistics(
                Namespace='AWS/EC2',
                MetricName='CPUUtilization',
                Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,
                Statistics=['Average']
            )

            datapoints = metrics.get('Datapoints', [])
            if datapoints:
                average_cpu = datapoints[0]['Average']
                if average_cpu < THRESHOLD:
                    idle_instances.append(instance_id)
                    report['idle_instances'].append({
                        "instance_id": instance_id,
                        "average_cpu": round(average_cpu, 2),
                        "timestamp": str(datapoints[0]['Timestamp'])
                    })

    if idle_instances:
        ec2.stop_instances(InstanceIds=idle_instances)
        report['stopped_instances'] = idle_instances

        # Upload report to S3
        timestamp_str = now.strftime("%Y-%m-%d_%H-%M-%S")
        key = f"idle-instances-{timestamp_str}.json"
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=json.dumps(report, indent=2),
            ContentType='application/json'
        )

        # Generate pre-signed URL (valid for 1 hour)
        presigned_url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET, 'Key': key},
            ExpiresIn=3600
        )

        # Prepare SNS message
        summary = f"""EC2 Cost Optimization Report - Auto-Shutdown Summary

Date: {now.strftime('%d %B %Y')}
Region: {REGION}
Triggered By: AWS Lambda via EventBridge

Summary:
- Total EC2 Instances Checked: {report['instances_checked']}
- Idle Instances Identified: {len(report['idle_instances'])}
- Instances Stopped: {len(report['stopped_instances'])}
- Estimated EC2 Cost (Yesterday): {report['estimated_cost']}  

Instance Actions:"""

        for item in report['idle_instances']:
            summary += f"\n- {item['instance_id']} | Average CPU: {item['average_cpu']}% | Time: {item['timestamp']}"

        summary += f"\n\nReport Download:\nS3 Pre-signed Link: {presigned_url}"
        summary += f"\n\nNext Check Scheduled: Every 1 hour"

        # Publish to SNS
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject='EC2 Idle Shutdown Report - ' + now.strftime('%d %B %Y'),
            Message=summary
        )
    else:
        print("No idle instances found. No SNS alert sent.")

    return {
        'statusCode': 200,
        'body': json.dumps('EC2 idle instance check complete.')
    }
